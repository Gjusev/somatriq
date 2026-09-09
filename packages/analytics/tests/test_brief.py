"""Golden-string tests for the deterministic morning brief (spec §102; ADR
0009 — pure rendering, no LLM). Goldens pin the exact wire format; partial
and empty days MUST render honest markers, never fabricated numbers (grill
decision: emit with marker beats skipping).
"""

from datetime import date

from somatriq_analytics.brief import build_morning_brief
from somatriq_analytics.recovery import baseline, recovery_v1
from somatriq_analytics.today_data import JournalToday, TodayData
from somatriq_contracts.recovery import HrvSummary, SleepSummary

DAY = date(2026, 8, 21)


def _today(
    *,
    recovery_inputs: dict[str, float | None],
    baselines: dict[str, tuple[float, float] | None],
    hrv_rmssd: float | None,
    sleep_minutes: float | None,
    resting_hr: float | None,
    coverage: float = 0.0,
) -> tuple[TodayData, float]:
    recovery = recovery_v1(day=DAY, inputs=recovery_inputs, baselines=baselines)
    data = TodayData(
        date=DAY,
        timezone="UTC",
        recovery=recovery,
        hrv=HrvSummary(day=DAY, rmssd_ms=hrv_rmssd) if hrv_rmssd is not None else None,
        sleep=(
            SleepSummary(day=DAY, duration_minutes=sleep_minutes)
            if sleep_minutes is not None
            else None
        ),
        resting_hr=resting_hr,
        resting_hr_quality="good" if coverage >= 0.5 else "insufficient",
        coverage_ratio=coverage,
        journal=JournalToday(caffeine_count=1),
    )
    return data, coverage


def test_full_data_golden() -> None:
    """Full day: §102 block format, percent-vs-baseline, insight, quality."""
    data, coverage = _today(
        recovery_inputs={"hrv": 106.0, "rhr": 60.0, "sleep": 441.0},
        baselines={
            # z' = +1.0 for hrv ((106-100)/(12/2)); 6% above the 100 median
            "hrv": (100.0, 12.0),
            # z' = 0 for rhr (60 on its median)
            "rhr": (60.0, 2.0),
            # z' = +1.0 for sleep ((441-431)/(20/2))
            "sleep": (431.0, 20.0),
        },
        hrv_rmssd=106.0,
        sleep_minutes=441.0,
        resting_hr=56.0,
        coverage=0.97,
    )
    # score = 100*(0.5 + (0.4+0.3)*tanh(1)/2) = 76.66 -> rendered as 77
    assert data.recovery.score is not None
    assert build_morning_brief(data, coverage) == (
        "Good morning\n"
        "\n"
        "Recovery\n"
        "77\n"
        "\n"
        "Sleep\n"
        "7 h 21 min\n"
        "\n"
        "HRV\n"
        "106 ms\n"
        "6 percent above baseline\n"
        "\n"
        "RHR\n"
        "56 bpm\n"
        "\n"
        "Main insight\n"
        "HRV higher than your recent average\n"
        "\n"
        "Data quality\n"
        "good — 97 percent coverage"
    )


def test_partial_data_golden_honest_markers() -> None:
    """Sleep + RHR present, HRV missing, no usable baselines: score null,
    HRV says no data, missing inputs listed — nothing fabricated."""
    data, coverage = _today(
        recovery_inputs={"hrv": None, "rhr": 60.0, "sleep": 441.0},
        baselines={"hrv": None, "rhr": None, "sleep": None},
        hrv_rmssd=None,
        sleep_minutes=441.0,
        resting_hr=60.0,
        coverage=0.4,
    )
    assert build_morning_brief(data, coverage) == (
        "Good morning\n"
        "\n"
        "Recovery\n"
        "not available yet\n"
        "\n"
        "Sleep\n"
        "7 h 21 min\n"
        "\n"
        "HRV\n"
        "no data yet\n"
        "\n"
        "RHR\n"
        "60 bpm\n"
        "\n"
        "Missing inputs\n"
        "hrv\n"
        "\n"
        "Data quality\n"
        "fair — 40 percent coverage"
    )


def test_empty_day_golden() -> None:
    """No data at all: every section honest, all inputs listed missing."""
    data, coverage = _today(
        recovery_inputs={"hrv": None, "rhr": None, "sleep": None},
        baselines={"hrv": None, "rhr": None, "sleep": None},
        hrv_rmssd=None,
        sleep_minutes=None,
        resting_hr=None,
        coverage=0.0,
    )
    assert build_morning_brief(data, coverage) == (
        "Good morning\n"
        "\n"
        "Recovery\n"
        "not available yet\n"
        "\n"
        "Sleep\n"
        "no data yet\n"
        "\n"
        "HRV\n"
        "no data yet\n"
        "\n"
        "RHR\n"
        "no data yet\n"
        "\n"
        "Missing inputs\n"
        "hrv, rhr, sleep\n"
        "\n"
        "Data quality\n"
        "insufficient — 0 percent coverage"
    )


def test_inputs_present_but_baselines_building_golden() -> None:
    """All inputs present, no baseline has 7 days: score null with an honest
    baselines note (not 'missing inputs'), HRV value still shown."""
    data, coverage = _today(
        recovery_inputs={"hrv": 106.0, "rhr": 60.0, "sleep": 441.0},
        baselines={"hrv": None, "rhr": None, "sleep": None},
        hrv_rmssd=106.0,
        sleep_minutes=441.0,
        resting_hr=60.0,
        coverage=0.6,
    )
    assert build_morning_brief(data, coverage) == (
        "Good morning\n"
        "\n"
        "Recovery\n"
        "not available yet\n"
        "\n"
        "Sleep\n"
        "7 h 21 min\n"
        "\n"
        "HRV\n"
        "106 ms\n"
        "baseline still building\n"
        "\n"
        "RHR\n"
        "60 bpm\n"
        "\n"
        "Missing inputs\n"
        "none — baselines still building (needs >= 7 days)\n"
        "\n"
        "Data quality\n"
        "good — 60 percent coverage"
    )


def test_hrv_below_baseline_and_substantial_insight() -> None:
    """Below-baseline percent renders signed-away; |z'| >= 2 earns
    'substantially'; rhr direction flips because lower raw HR is positive."""
    data, coverage = _today(
        recovery_inputs={"hrv": 80.0, "rhr": 66.0, "sleep": 421.0},
        baselines={
            "hrv": (100.0, 20.0),  # z' = -2.0
            "rhr": (60.0, 4.0),  # z' = -(66-60)/2 = -3.0 -> strongest
            "sleep": (431.0, 20.0),  # z' = -1.0
        },
        hrv_rmssd=80.0,
        sleep_minutes=421.0,
        resting_hr=66.0,
        coverage=0.55,
    )
    brief = build_morning_brief(data, coverage)
    assert "HRV\n80 ms\n20 percent below baseline" in brief
    assert "Main insight\nResting heart rate substantially higher than your recent average" in brief


def test_neutral_insight_and_in_line_percent() -> None:
    """All z' inside the neutral band: 'in line' insight; 0% delta renders
    'in line with baseline'."""
    data, coverage = _today(
        recovery_inputs={"hrv": 100.0, "rhr": 60.0, "sleep": 431.0},
        baselines={"hrv": (100.0, 12.0), "rhr": (60.0, 4.0), "sleep": (431.0, 20.0)},
        hrv_rmssd=100.0,
        sleep_minutes=431.0,
        resting_hr=60.0,
        coverage=0.99,
    )
    brief = build_morning_brief(data, coverage)
    assert "HRV\n100 ms\nin line with baseline" in brief
    assert "Main insight\nHRV in line with your recent average" in brief


def test_brief_is_deterministic() -> None:
    """Same input, same string — twice, byte for byte (ADR 0009)."""
    data, coverage = _today(
        recovery_inputs={"hrv": 106.0, "rhr": 60.0, "sleep": 441.0},
        baselines={"hrv": (100.0, 12.0), "rhr": (60.0, 2.0), "sleep": (431.0, 20.0)},
        hrv_rmssd=106.0,
        sleep_minutes=441.0,
        resting_hr=56.0,
        coverage=0.97,
    )
    assert build_morning_brief(data, coverage) == build_morning_brief(data, coverage)


def test_baseline_helper_feeds_brief_without_invention() -> None:
    """baseline() returns None under the 7-day minimum and the brief says so
    — the two pieces compose without ever faking a baseline."""
    assert baseline([100.0, 101.0]) is None
    assert baseline([float(i) for i in range(7)]) is not None


# ── Plan section (Block 1) ─────────────────────────────────────────────────

STRAIN_20 = [float(v) for v in range(10, 210, 10)]


def _plan_data(*, degraded: bool = False):
    from datetime import time as time_type

    from somatriq_analytics.day_plan import day_plan_v1
    from somatriq_analytics.plan_data import PlanData
    from somatriq_analytics.sleep_need import SleepDebt, sleep_need_v1

    if degraded:
        need = sleep_need_v1(
            DAY, baseline_sleep_min=None, sleep_debt_min=None, recent_load=None, recovery=None
        )
        debt = SleepDebt(debt_min=None, measured_days=0, unmeasured_days=7)
        plan = day_plan_v1(
            DAY,
            recovery_score=None,
            sleep_debt_min=None,
            sleep_need_minutes=None,
            strain_history=[],
            wake_time=time_type(7, 0),
        )
    else:
        need = sleep_need_v1(
            DAY, baseline_sleep_min=480.0, sleep_debt_min=60.0, recent_load=None, recovery=40.0
        )
        debt = SleepDebt(debt_min=60.0, measured_days=7, unmeasured_days=0)
        plan = day_plan_v1(
            DAY,
            recovery_score=60.0,
            sleep_debt_min=60.0,
            sleep_need_minutes=need.minutes,
            strain_history=STRAIN_20,
            wake_time=time_type(7, 0),
        )
    return PlanData(
        date=DAY,
        timezone="UTC",
        wake_time=time_type(7, 0),
        wake_source="default",
        sleep_baseline=(480.0, 0.0),
        sleep_debt=debt,
        sleep_need=need,
        plan=plan,
    )


def test_brief_with_plan_renders_plan_section() -> None:
    data, coverage = _today(
        recovery_inputs={"hrv": 106.0, "rhr": 60.0, "sleep": 441.0},
        baselines={"hrv": (100.0, 12.0), "rhr": (60.0, 2.0), "sleep": (431.0, 20.0)},
        hrv_rmssd=106.0,
        sleep_minutes=441.0,
        resting_hr=56.0,
        coverage=0.97,
    )
    text = build_morning_brief(data, coverage, _plan_data())
    assert "guidance: moderate (target strain 105-152.5)" in text
    assert "in bed by 22:05-22:35" in text
    assert "sleep need: 8 h 40 min" in text
    assert "sleep debt (7d, capped): 60 min" in text


def test_brief_without_plan_has_no_plan_section() -> None:
    data, coverage = _today(
        recovery_inputs={"hrv": None, "rhr": None, "sleep": None},
        baselines={"hrv": None, "rhr": None, "sleep": None},
        hrv_rmssd=None,
        sleep_minutes=None,
        resting_hr=None,
    )
    assert "Plan" not in build_morning_brief(data, coverage)


def test_brief_plan_degrades_without_fabrication() -> None:
    data, coverage = _today(
        recovery_inputs={"hrv": None, "rhr": None, "sleep": None},
        baselines={"hrv": None, "rhr": None, "sleep": None},
        hrv_rmssd=None,
        sleep_minutes=None,
        resting_hr=None,
    )
    text = build_morning_brief(data, coverage, _plan_data(degraded=True))
    assert "guidance: not available yet — recovery not earned" in text
    assert "sleep need:" not in text
    assert "in bed by" not in text
