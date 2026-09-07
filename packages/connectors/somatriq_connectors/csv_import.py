"""Pure CSV import parsing and preview (spec §129; M13).

Accepted grammar — deliberately small (a personal health CSV, not a
spreadsheet engine):

* one header row; the field separator is comma or semicolon, sniffed from
  the header line (whichever occurs more often; ties go to comma);
* a required ``date`` column (case-insensitive): ISO ``yyyy-mm-dd`` or
  European ``dd.mm.yyyy``;
* every other column matches case-insensitively against the frozen
  VENDOR_DAILY_METRICS catalog — wire names (``resting_hr``) and their NOOP
  column aliases (``restingHr``) both work; unknown columns are collected
  and warned, never dropped silently; a repeated known column replaces the
  earlier mapping (last wins, mirroring row semantics);
* values are floats with an optional single comma decimal (``84,5`` → 84.5,
  only when no dot is present); empty cells mean "not reported" and are
  omitted (the contract omits nulls);
* physiologic range gates: weight 30–300 kg, body fat 3–70 % — a value
  outside its gate is a row-level warning and the value is skipped;
* the same (day, metric) twice in one file counts as an in-file duplicate
  and the LAST value wins (documented, warned, counted).

Warning ``line`` numbers are 1-based record numbers (the header is line 1),
which equals the physical line for ordinary single-line records.

This module is pure: no HTTP, no database. The API layer (imports.py) owns
transport, the preview token and the already-in-DB duplicate lookup.
"""

import csv
import math
import re
from dataclasses import dataclass
from datetime import date

from somatriq_contracts.observations import VENDOR_DAILY_METRICS, DailyObservationItem

# Preview display cap: the §129 warning list is capped for the wire, the
# total count always travels alongside (never a silent truncation).
MAX_DISPLAY_WARNINGS = 20

# Physiologic range gates for the two body-composition metrics (§129
# validation warnings; absent vendor-reported metrics have no gate).
WEIGHT_KG_MIN, WEIGHT_KG_MAX = 30.0, 300.0
BODY_FAT_PERCENT_MIN, BODY_FAT_PERCENT_MAX = 3.0, 70.0

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EUROPEAN_DAY = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")

# Header cell (lowercased) → canonical wire metric name. Wire names first,
# then the NOOP column aliases (weight/body-fat have none: "—").
_HEADER_ALIASES: dict[str, str] = {name.lower(): name for name in VENDOR_DAILY_METRICS}
for _wire, _noop in VENDOR_DAILY_METRICS.items():
    if _noop != "—":
        _HEADER_ALIASES.setdefault(_noop.lower(), _wire)


class CsvImportError(ValueError):
    """Unrecoverable import shape error (empty file, no header, no date column)."""


@dataclass(frozen=True)
class CsvWarning:
    """One validation finding, tied to its 1-based line number."""

    line: int
    reason: str


@dataclass(frozen=True)
class ImportDuplicates:
    """§129 duplicates: repeats inside the file + pairs already in the DB."""

    in_file: int
    already_present: int


@dataclass(frozen=True)
class CsvParseResult:
    """Everything parse_daily_csv learned about one file."""

    rows: list[DailyObservationItem]  # deduplicated (last-wins), sorted (day, metric)
    warnings: list[CsvWarning]  # every warning, in file order
    skipped_columns: list[str]  # unknown header columns, original spelling
    duplicate_count: int  # in-file (day, metric) repeats


@dataclass(frozen=True)
class ImportPreview:
    """The exact §129 preview fields (plus the display cap bookkeeping)."""

    date_range: tuple[date, date] | None  # (first_day, last_day); None when no rows
    metrics: list[tuple[str, int]]  # metric name → deduplicated row count, sorted
    record_count: int
    duplicates: ImportDuplicates
    validation_warnings: list[CsvWarning]  # capped to MAX_DISPLAY_WARNINGS
    validation_warning_count: int  # total (uncapped)
    skipped_columns: list[str]


def _parse_day(raw: str) -> date | None:
    """ISO yyyy-mm-dd or European dd.mm.yyyy; None when neither."""
    candidate = raw.strip()
    if _ISO_DAY.match(candidate):
        return date.fromisoformat(candidate)
    if _EUROPEAN_DAY.match(candidate):
        day_part, month_part, year_part = candidate.split(".")
        return date(int(year_part), int(month_part), int(day_part))
    return None


def _parse_float(raw: str) -> float | None:
    """Float with an optional single comma decimal; None when unparseable.

    ``84,5`` parses as 84.5 only when the cell has no dot and exactly one
    comma; multi-comma values (thousands separators) stay unparseable and
    surface as a warning rather than a guessed number.
    """
    candidate = raw.strip()
    try:
        value = float(candidate)
    except ValueError:
        if candidate.count(",") != 1 or "." in candidate:
            return None
        try:
            value = float(candidate.replace(",", "."))
        except ValueError:
            return None
    return value if math.isfinite(value) else None


def _sniff_delimiter(header_line: str) -> str:
    """Comma or semicolon — whichever the header uses more; ties → comma."""
    return ";" if header_line.count(";") > header_line.count(",") else ","


def parse_daily_csv(text: str) -> CsvParseResult:
    """Parse one daily-metrics CSV into canonical observation candidates."""
    stripped = text.lstrip(chr(0xFEFF))  # tolerate a UTF-8 BOM
    if not stripped.strip():
        raise CsvImportError("file is empty")
    lines = stripped.splitlines()
    records = list(csv.reader(lines, delimiter=_sniff_delimiter(lines[0])))
    if not records or not any(cell.strip() for cell in records[0]):
        raise CsvImportError("file has no header row")

    header = [cell.strip() for cell in records[0]]
    date_index: int | None = None
    column_metrics: dict[int, str] = {}
    skipped_columns: list[str] = []
    for index, column in enumerate(header):
        normalized = column.lower()
        if normalized == "date":
            date_index = index  # a repeated date column: last wins
        elif normalized in _HEADER_ALIASES:
            column_metrics[index] = _HEADER_ALIASES[normalized]  # repeated: last wins
        else:
            skipped_columns.append(column)
    if date_index is None:
        raise CsvImportError(f"missing required 'date' column; header was: {header}")

    warnings: list[CsvWarning] = []
    if skipped_columns:
        warnings.append(CsvWarning(1, f"unknown columns skipped: {', '.join(skipped_columns)}"))

    values: dict[tuple[date, str], float] = {}
    duplicate_count = 0
    for line_number, cells in enumerate(records[1:], start=2):
        if not any(cell.strip() for cell in cells):
            continue  # wholly empty line
        raw_day = cells[date_index].strip() if date_index < len(cells) else ""
        if not raw_day:
            warnings.append(CsvWarning(line_number, "row has no date — skipped"))
            continue
        day = _parse_day(raw_day)
        if day is None:
            warnings.append(CsvWarning(line_number, f"unparseable date {raw_day!r} — row skipped"))
            continue
        for index, metric in column_metrics.items():
            if index >= len(cells):
                continue
            raw_value = cells[index].strip()
            if not raw_value:
                continue  # not reported → omitted, not warned
            value = _parse_float(raw_value)
            if value is None:
                warnings.append(
                    CsvWarning(
                        line_number,
                        f"{metric}: non-numeric value {raw_value!r} — skipped",
                    )
                )
                continue
            if metric == "weight_kg" and not WEIGHT_KG_MIN <= value <= WEIGHT_KG_MAX:
                warnings.append(
                    CsvWarning(
                        line_number,
                        f"weight_kg {value:g} outside "
                        f"{WEIGHT_KG_MIN:g}–{WEIGHT_KG_MAX:g} kg — skipped",
                    )
                )
                continue
            if (
                metric == "body_fat_percent"
                and not BODY_FAT_PERCENT_MIN <= value <= BODY_FAT_PERCENT_MAX
            ):
                warnings.append(
                    CsvWarning(
                        line_number,
                        f"body_fat_percent {value:g} outside "
                        f"{BODY_FAT_PERCENT_MIN:g}–{BODY_FAT_PERCENT_MAX:g} % — skipped",
                    )
                )
                continue
            key = (day, metric)
            if key in values:
                duplicate_count += 1
                warnings.append(
                    CsvWarning(
                        line_number,
                        f"duplicate ({day.isoformat()}, {metric}) — last value wins",
                    )
                )
            values[key] = value

    rows = [
        DailyObservationItem(day=day, metric=metric, value=value)
        for (day, metric), value in sorted(values.items())
    ]
    return CsvParseResult(
        rows=rows,
        warnings=warnings,
        skipped_columns=skipped_columns,
        duplicate_count=duplicate_count,
    )


def build_preview(
    parse_result: CsvParseResult,
    existing_pairs: frozenset[tuple[date, str]] = frozenset(),
) -> ImportPreview:
    """Shape a parse result into the §129 preview.

    ``existing_pairs`` is the set of (day, metric) pairs already stored for
    this user — the API layer supplies it from the database; the pure layer
    never queries. Rows that parse but already exist are counted as
    ``already_present`` duplicates (they will be no-ops on commit).
    """
    day_values = [item.day for item in parse_result.rows]
    metric_counts: dict[str, int] = {}
    for item in parse_result.rows:
        metric_counts[item.metric] = metric_counts.get(item.metric, 0) + 1
    already_present = sum(
        1 for item in parse_result.rows if (item.day, item.metric) in existing_pairs
    )
    return ImportPreview(
        date_range=(min(day_values), max(day_values)) if day_values else None,
        metrics=sorted(metric_counts.items()),
        record_count=len(parse_result.rows),
        duplicates=ImportDuplicates(
            in_file=parse_result.duplicate_count,
            already_present=already_present,
        ),
        validation_warnings=parse_result.warnings[:MAX_DISPLAY_WARNINGS],
        validation_warning_count=len(parse_result.warnings),
        skipped_columns=list(parse_result.skipped_columns),
    )
