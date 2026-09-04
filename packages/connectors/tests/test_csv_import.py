"""Pure CSV parse/preview behavior (spec §129; M13).

Header and separator variants, the comma-decimal rule, range gates,
in-file duplicates, unknown columns, and the exact §129 preview fields.
No HTTP, no database — this is the connectors package's pure core.
"""

from datetime import date

import pytest
from somatriq_connectors.csv_import import (
    MAX_DISPLAY_WARNINGS,
    CsvImportError,
    build_preview,
    parse_daily_csv,
)


def test_parses_iso_dates_with_dot_decimals() -> None:
    result = parse_daily_csv(
        "date,weight_kg,body_fat_percent\n2026-01-05,84.5,17.2\n2026-01-06,84.1,\n"
    )
    assert [(row.day, row.metric, row.value) for row in result.rows] == [
        (date(2026, 1, 5), "body_fat_percent", 17.2),
        (date(2026, 1, 5), "weight_kg", 84.5),
        (date(2026, 1, 6), "weight_kg", 84.1),
    ]
    assert result.warnings == []
    assert result.skipped_columns == []
    assert result.duplicate_count == 0


def test_parses_european_dates_semicolons_and_comma_decimals() -> None:
    result = parse_daily_csv("date;weight_kg\n05.01.2026;84,5\n")
    assert [(row.day, row.metric, row.value) for row in result.rows] == [
        (date(2026, 1, 5), "weight_kg", 84.5)
    ]


def test_header_is_case_insensitive_and_accepts_noop_aliases() -> None:
    result = parse_daily_csv("Date,Weight_KG,RESTINGHR,recovery\n2026-02-01,80.0,52,0.81\n")
    by_metric = {row.metric: row.value for row in result.rows}
    assert by_metric == {"weight_kg": 80.0, "resting_hr": 52.0, "recovery": 0.81}


def test_unknown_columns_are_listed_and_warned_never_silent() -> None:
    result = parse_daily_csv("date,weight_kg,mood,notes\n2026-01-05,80.0,happy,hello\n")
    assert result.skipped_columns == ["mood", "notes"]
    assert len(result.warnings) == 1
    assert result.warnings[0].line == 1
    assert "mood" in result.warnings[0].reason
    assert "notes" in result.warnings[0].reason
    # The known column still parsed.
    assert [(row.metric, row.value) for row in result.rows] == [("weight_kg", 80.0)]


def test_missing_date_column_is_a_clean_error() -> None:
    with pytest.raises(CsvImportError, match="date"):
        parse_daily_csv("weight_kg\n80.0\n")


def test_empty_file_is_a_clean_error() -> None:
    with pytest.raises(CsvImportError, match="empty"):
        parse_daily_csv("")
    with pytest.raises(CsvImportError, match="empty"):
        parse_daily_csv("   \n\n")


def test_header_only_file_is_a_clean_error() -> None:
    # No header row content at all (garbage) → error, not an empty preview.
    with pytest.raises(CsvImportError, match="header"):
        parse_daily_csv(";;;")



def test_garbage_binary_is_a_clean_error() -> None:
    with pytest.raises(CsvImportError):
        parse_daily_csv("\x00\x01\x02")


def test_unparseable_date_row_warns_with_line_number() -> None:
    result = parse_daily_csv(
        "date,weight_kg\n2026-01-05,80.0\nJan 5,81.0\n2026-01-07,82.0\n"
    )
    assert len(result.rows) == 2
    assert [(w.line, w.reason) for w in result.warnings] == [
        (3, "unparseable date 'Jan 5' — row skipped")
    ]


def test_non_numeric_value_warns_and_skips_only_that_value() -> None:
    result = parse_daily_csv("date,weight_kg,steps\n2026-01-05,abc,9000\n")
    assert [(row.metric, row.value) for row in result.rows] == [("steps", 9000.0)]
    assert [(w.line, w.reason) for w in result.warnings] == [
        (2, "weight_kg: non-numeric value 'abc' — skipped")
    ]


def test_multi_comma_value_is_not_a_guessed_number() -> None:
    result = parse_daily_csv("date;steps\n2026-01-05;1,234,567\n")
    assert result.rows == []
    assert "non-numeric" in result.warnings[0].reason


def test_weight_range_gate_warns_and_skips() -> None:
    result = parse_daily_csv(
        "date,weight_kg\n2026-01-05,420.0\n2026-01-06,20.0\n2026-01-07,80.0\n"
    )
    assert [(row.day, row.value) for row in result.rows] == [(date(2026, 1, 7), 80.0)]
    assert [w.line for w in result.warnings] == [2, 3]
    assert all("weight_kg" in w.reason for w in result.warnings)


def test_body_fat_range_gate_warns_and_skips() -> None:
    result = parse_daily_csv("date,body_fat_percent\n2026-01-05,90.0\n2026-01-06,17.0\n")
    assert [(row.day, row.value) for row in result.rows] == [(date(2026, 1, 6), 17.0)]
    assert "body_fat_percent" in result.warnings[0].reason


def test_in_file_duplicates_counted_last_wins() -> None:
    result = parse_daily_csv(
        "date,weight_kg\n2026-01-05,80.0\n2026-01-05,81.5\n2026-01-06,82.0\n"
    )
    assert [(row.day, row.value) for row in result.rows] == [
        (date(2026, 1, 5), 81.5),
        (date(2026, 1, 6), 82.0),
    ]
    assert result.duplicate_count == 1
    assert [(w.line, w.reason) for w in result.warnings] == [
        (3, "duplicate (2026-01-05, weight_kg) — last value wins")
    ]


def test_empty_cells_are_omitted_not_warned() -> None:
    result = parse_daily_csv("date,weight_kg,steps\n2026-01-05,,\n")
    assert result.rows == []
    assert result.warnings == []


def test_blank_lines_are_skipped_without_warnings() -> None:
    result = parse_daily_csv("date,weight_kg\n\n2026-01-05,80.0\n\n")
    assert len(result.rows) == 1
    assert result.warnings == []


def test_utf8_bom_is_tolerated() -> None:
    result = parse_daily_csv("﻿date,weight_kg\n2026-01-05,80.0\n")
    assert len(result.rows) == 1
    assert result.warnings == []


# ── preview: the exact §129 fields ───────────────────────────────────────


def test_preview_carries_the_exact_spec_fields() -> None:
    parse = parse_daily_csv(
        "date,weight_kg,steps,mood\n"
        "2026-01-05,80.0,9000,ok\n"
        "2026-01-05,81.0,9500,ok\n"  # in-file duplicate (weight + steps)
        "2026-01-07,82.0,abc,ok\n"  # non-numeric steps
    )
    preview = build_preview(parse)
    assert preview.date_range == (date(2026, 1, 5), date(2026, 1, 7))
    assert preview.metrics == [("steps", 1), ("weight_kg", 2)]
    assert preview.record_count == 3
    assert preview.duplicates.in_file == 2
    assert preview.duplicates.already_present == 0
    assert preview.validation_warning_count == 4  # unknown col + 2 dups + non-numeric
    assert [w.reason for w in preview.validation_warnings] == [
        w.reason for w in parse.warnings[:MAX_DISPLAY_WARNINGS]
    ]
    assert preview.skipped_columns == ["mood"]


def test_preview_warning_list_is_capped_total_travels_alongside() -> None:
    lines = ["date,weight_kg"]
    for day in range(1, 26):  # 25 bad-value warnings
        lines.append(f"2026-01-{day:02d},oops")
    parse = parse_daily_csv("\n".join(lines) + "\n")
    preview = build_preview(parse)
    assert preview.validation_warning_count == 25
    assert len(preview.validation_warnings) == MAX_DISPLAY_WARNINGS


def test_preview_counts_already_present_pairs() -> None:
    parse = parse_daily_csv(
        "date,weight_kg,steps\n2026-01-05,80.0,9000\n2026-01-06,81.0,9100\n"
    )
    existing = frozenset({(date(2026, 1, 5), "weight_kg"), (date(2026, 1, 6), "steps")})
    preview = build_preview(parse, existing)
    assert preview.duplicates.already_present == 2
    assert preview.duplicates.in_file == 0
    assert preview.record_count == 4


def test_preview_of_header_only_parse_has_no_date_range() -> None:
    # A file with a valid header, a broken-date row and nothing else parses
    # (warned) into zero rows: date_range is None, record_count 0.
    parse = parse_daily_csv("date,weight_kg\nnot-a-date,80.0\n")
    preview = build_preview(parse)
    assert preview.date_range is None
    assert preview.record_count == 0
    assert preview.metrics == []
    assert preview.validation_warning_count == 1
