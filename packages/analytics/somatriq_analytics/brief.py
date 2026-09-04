"""Deterministic morning-brief text (spec §102; grill decision on partial
data: emit WITH coverage markers, never present insufficient data as solid).

Pure string assembly over TodayData — no LLM anywhere (ADR 0009). Every
line is derived from data that is actually present; anything missing is
rendered as "no data yet" (or listed under Missing inputs) and the brief
always ends with a Data quality line carrying grade + coverage percent.

Format follows the spec §102 example: label on its own line, value below,
sections separated by blank lines. The Main insight line is computed
deterministically from the largest |robust z'| recovery contribution
(spec §76 explainability, in plain language).
"""

from somatriq_contracts.daily import grade_quality
from somatriq_contracts.recovery import RECOVERY_BASELINE_MIN_DAYS

from .today_data import TodayData

# |z'| at which the insight earns "substantially" (mirrors the spec §102
# example wording "increased substantially"; the recovery label thresholds
# stay owned by the frozen contract).
_SUBSTANTIAL_Z = 2.0

# Plain-language direction per input. "positive" = the input moved the
# RECOVERY SCORE up; for rhr that means the raw heart rate went DOWN —
# orientation is already baked into z' (spec §76).
_SUBJECT: dict[str, str] = {
    "hrv": "HRV",
    "rhr": "Resting heart rate",
    "sleep": "Sleep duration",
}
_UP: dict[str, str] = {"hrv": "higher", "rhr": "lower", "sleep": "longer"}
_DOWN: dict[str, str] = {"hrv": "lower", "rhr": "higher", "sleep": "shorter"}


def _sleep_line(data: TodayData) -> str:
    minutes = data.sleep.duration_minutes if data.sleep is not None else None
    if minutes is None:
        return "no data yet"
    total = int(round(minutes))
    return f"{total // 60} h {total % 60} min"


def _hrv_lines(data: TodayData) -> list[str]:
    if data.hrv is None or data.hrv.rmssd_ms is None:
        return ["no data yet"]
    lines = [f"{data.hrv.rmssd_ms:g} ms"]
    hrv_input = next(
        (c for c in data.recovery.contributions if c.input == "hrv"), None
    )
    if hrv_input is None or hrv_input.baseline_median is None:
        return lines + ["baseline still building"]
    median = hrv_input.baseline_median
    if median <= 0:
        return lines  # no defensible ratio; the value stands on its own
    percent = round((data.hrv.rmssd_ms / median - 1) * 100)
    if percent > 0:
        lines.append(f"{percent} percent above baseline")
    elif percent < 0:
        lines.append(f"{abs(percent)} percent below baseline")
    else:
        lines.append("in line with baseline")
    return lines


def _main_insight(data: TodayData) -> str | None:
    """Largest |z'| contribution direction in plain language (deterministic).

    Ties resolve to the first contribution in the frozen recovery order
    (hrv, rhr, sleep) — ``max`` keeps the first maximal element.
    """
    scored = [c for c in data.recovery.contributions if c.robust_z is not None]
    if not scored:
        return None
    strongest = max(scored, key=lambda c: abs(c.robust_z or 0.0))
    if strongest.contribution == "neutral":
        return f"{_SUBJECT[strongest.input]} in line with your recent average"
    direction = (
        _UP[strongest.input] if strongest.contribution == "positive" else _DOWN[strongest.input]
    )
    adverb = "substantially " if abs(strongest.robust_z or 0.0) >= _SUBSTANTIAL_Z else ""
    return f"{_SUBJECT[strongest.input]} {adverb}{direction} than your recent average"


def _missing_inputs_line(data: TodayData) -> str | None:
    if data.recovery.score is not None:
        return None
    if data.recovery.missing_inputs:
        return ", ".join(data.recovery.missing_inputs)
    # Score null with no missing inputs means the baselines are not usable
    # yet — say so instead of implying the inputs are at fault.
    return f"none — baselines still building (needs >= {RECOVERY_BASELINE_MIN_DAYS} days)"


def build_morning_brief(today_data: TodayData, coverage: float) -> str:
    """Render the §102 morning brief. ``coverage`` is the day's data coverage
    fraction in [0, 1] (spec §99) — it is ALWAYS shown, never fabricated.
    """
    sections: list[list[str]] = [["Good morning"]]

    score = today_data.recovery.score
    sections.append(["Recovery", f"{round(score)}" if score is not None else "not available yet"])
    sections.append(["Sleep", _sleep_line(today_data)])
    sections.append(["HRV", *_hrv_lines(today_data)])
    sections.append(
        [
            "RHR",
            (
                f"{today_data.resting_hr:g} bpm"
                if today_data.resting_hr is not None
                else "no data yet"
            ),
        ]
    )

    insight = _main_insight(today_data)
    if insight is not None:
        sections.append(["Main insight", insight])

    missing = _missing_inputs_line(today_data)
    if missing is not None:
        sections.append(["Missing inputs", missing])

    percent = round(coverage * 100)
    sections.append(["Data quality", f"{grade_quality(coverage)} — {percent} percent coverage"])

    return "\n\n".join("\n".join(section) for section in sections)
