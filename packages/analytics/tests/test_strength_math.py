"""Strength math + the deterministic training parser (spec §78-80, §104,
§205 M12; ADR 0009 — deterministic Python, never an LLM).

Every number below is hand-computed:

* Epley e1RM: weight * (1 + effective_reps / 30) with effective_reps =
  reps + RIR when RIR is stated (a set taken to RIR=2 could have produced
  ~2 more reps — the estimate uses the reps the lifter COULD have done).
  180 kg × 8 → 180 * (1 + 8/30) = 228.0 exactly;
  180 kg × 8 @ RIR 2 → 180 * (1 + 10/30) = 240.0 exactly.
* The spec §104 example "Chest press 180x8 170x9 160x10":
  tonnage = 1440 + 1530 + 1600 = 4570 kg, best e1RM = 228.0.
"""

import math

import pytest
from somatriq_analytics.strength import (
    EXERCISE_CATALOG,
    ParsedSet,
    StrengthSet,
    estimate_1rm,
    muscle_group_for,
    parse_training_line,
    session_summary,
)

# ── the spec §104 example, exact ──────────────────────────────────────────


def test_spec_example_parses_exactly() -> None:
    parsed = parse_training_line("Chest press 180x8 170x9 160x10")
    assert parsed is not None
    assert parsed.exercise == "chest press"
    assert parsed.sets == [
        ParsedSet(weight_kg=180.0, reps=8, rir=None, rpe=None),
        ParsedSet(weight_kg=170.0, reps=9, rir=None, rpe=None),
        ParsedSet(weight_kg=160.0, reps=10, rir=None, rpe=None),
    ]


def test_spec_example_summary_exact() -> None:
    parsed = parse_training_line("Chest press 180x8 170x9 160x10")
    assert parsed is not None
    summary = session_summary(parsed.to_strength_sets())
    assert summary.set_count == 3
    assert summary.tonnage_kg == pytest.approx(4570.0)
    assert summary.bodyweight_sets == 0
    assert summary.exercises == ["chest press"]
    assert summary.best_e1rm_by_exercise == {"chest press": pytest.approx(228.0)}
    assert summary.volume_by_group == {"chest": pytest.approx(4570.0)}
    # e1RMs: 180*(1+8/30)=228, 170*(1+9/30)=221, 160*(1+10/30)=213.33
    # ratios: 1.0, 221/228, 213.3../228; volume-weighted mean of ratios:
    ratios = [228.0 / 228.0, 221.0 / 228.0, (160.0 * 4.0 / 3.0) / 228.0]
    volumes = [1440.0, 1530.0, 1600.0]
    expected = sum(r * v for r, v in zip(ratios, volumes, strict=True)) / sum(volumes)
    assert summary.relative_intensity == pytest.approx(expected)


# ── grammar variants ──────────────────────────────────────────────────────


def test_uppercase_x_and_unicode_times_separator() -> None:
    for line in ("Bench press 100X5", "Bench press 100×5", "bench press 100x5"):
        parsed = parse_training_line(line)
        assert parsed is not None
        assert parsed.exercise == "bench press"
        assert parsed.sets == [ParsedSet(100.0, 5, None, None)]


def test_decimal_weight_with_comma_or_dot() -> None:
    parsed = parse_training_line("Squat 102,5x8")
    assert parsed is not None
    assert parsed.sets[0].weight_kg == pytest.approx(102.5)
    parsed = parse_training_line("Squat 102.5x8")
    assert parsed is not None
    assert parsed.sets[0].weight_kg == pytest.approx(102.5)


def test_bodyweight_bare_reps() -> None:
    parsed = parse_training_line("pull-ups 8 8 7")
    assert parsed is not None
    assert parsed.exercise == "pull ups"
    assert parsed.sets == [
        ParsedSet(None, 8, None, None),
        ParsedSet(None, 8, None, None),
        ParsedSet(None, 7, None, None),
    ]
    summary = session_summary(parsed.to_strength_sets())
    assert summary.bodyweight_sets == 3
    assert summary.tonnage_kg == 0.0  # bodyweight tonnage is 0, never invented
    assert summary.relative_intensity is None  # no external load → no e1RM
    assert summary.hard_sets == 0  # no RIR/RPE and no load → never guessed hard


def test_rir_per_group() -> None:
    parsed = parse_training_line("Squat 140x5 rir=2 130x8 rir=4")
    assert parsed is not None
    assert [(s.rir, s.rpe) for s in parsed.sets] == [(2, None), (4, None)]


def test_rpe_once_at_end_applies_to_all_sets() -> None:
    parsed = parse_training_line("Row 80x8 75x10 rpe=8")
    assert parsed is not None
    assert [(s.rir, s.rpe) for s in parsed.sets] == [(None, 8.0), (None, 8.0)]


def test_rir_once_at_end_applies_to_all_sets() -> None:
    parsed = parse_training_line("Squat 140x5 130x8 rir=3")
    assert parsed is not None
    assert [(s.rir, s.rpe) for s in parsed.sets] == [(3, None), (3, None)]


def test_rir_and_rpe_together_at_end() -> None:
    parsed = parse_training_line("Dip 40x8 rir=1 rpe=9")
    assert parsed is not None
    assert parsed.sets[0].rir == 1
    assert parsed.sets[0].rpe == pytest.approx(9.0)


# ── NOT a training line → None (falls through to /log) ────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "I drank a coffee now",  # the §103 example must stay a caffeine event
        "coffee 2",  # single bare number is a quantity, not a rep pattern
        "tired today",
        "hello",
        "",
        "note 180",  # one bare number is never a set
        "180",  # no exercise words
    ],
)
def test_non_training_lines_return_none(line: str) -> None:
    assert parse_training_line(line) is None


# ── exercise catalog ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exercise", "group"),
    [
        ("squat", "quads"),
        ("front squat", "quads"),
        ("deadlift", "posterior_chain"),
        ("rdl", "posterior_chain"),
        ("bench press", "chest"),
        ("incline bench", "chest"),
        ("chest press", "chest"),
        ("ohp", "shoulders"),
        ("overhead press", "shoulders"),
        ("military press", "shoulders"),
        ("row", "back"),
        ("barbell row", "back"),
        ("pull ups", "back"),
        ("lat pulldown", "back"),
        ("dip", "chest"),  # chest/triceps — one primary group, documented
        ("biceps curl", "arms"),
        ("curls", "arms"),
        ("triceps extension", "arms"),
        ("lateral raise", "shoulders"),
        ("lunge", "quads"),
        ("hip thrust", "posterior_chain"),
        ("calf raise", "calves"),
        ("plank", "core"),
    ],
)
def test_catalog_primary_groups(exercise: str, group: str) -> None:
    assert muscle_group_for(exercise) == group


def test_unknown_exercise_is_other_never_a_guess() -> None:
    assert muscle_group_for("fliegel press") == "other"


def test_catalog_is_at_least_sixteen_exercises() -> None:
    assert len(EXERCISE_CATALOG) >= 16


def test_incline_bench_press_resolves_to_incline_bench_not_bench() -> None:
    assert muscle_group_for("incline bench press") == "chest"
    parsed = parse_training_line("Incline bench press 100x8")
    assert parsed is not None
    assert parsed.exercise == "incline bench press"


# ── estimate_1rm: Epley with RIR-adjusted reps ────────────────────────────


def test_e1rm_epley_exact_numbers() -> None:
    assert estimate_1rm(180.0, 8) == pytest.approx(228.0)
    assert estimate_1rm(180.0, 8, rir=2) == pytest.approx(240.0)
    assert estimate_1rm(100.0, 1) == pytest.approx(100.0 * (1 + 1 / 30))
    assert estimate_1rm(100.0, 1, rir=0) == pytest.approx(100.0 * (1 + 1 / 30))


def test_e1rm_bodyweight_is_zero_documented() -> None:
    """No external load → nothing to estimate from; 0.0, never a guess."""
    assert estimate_1rm(None, 8) == 0.0


# ── hard sets ─────────────────────────────────────────────────────────────


def test_hard_sets_by_rir_and_rpe() -> None:
    sets = [
        StrengthSet("bench press", 100.0, 8, rir=2),  # RIR <= 2 → hard
        StrengthSet("bench press", 90.0, 8, rir=4),  # not hard by RIR
        StrengthSet("bench press", 80.0, 10, rpe=8.0),  # RPE >= 8 → hard
        StrengthSet("bench press", 70.0, 10, rpe=6.5),  # not hard
    ]
    summary = session_summary(sets)
    assert summary.hard_sets == 2


def test_hard_sets_fallback_85_percent_of_session_best_e1rm() -> None:
    """No RIR/RPE → a set is hard when its e1RM is >= 85% of the session's
    best e1RM FOR THAT EXERCISE (the fallback is per-exercise, not global)."""
    sets = [
        StrengthSet("squat", 140.0, 5),  # e1RM 163.33 = session best (squat)
        StrengthSet("squat", 110.0, 5),  # e1RM 128.33 = 78.6% → not hard
        StrengthSet("squat", 120.0, 5),  # e1RM 140.0 = 85.7% → hard
        StrengthSet("row", 80.0, 8),  # row session best 96; its own 100%
    ]
    summary = session_summary(sets)
    assert summary.hard_sets == 3  # squat sets 1+3, row set (100% of its best)


def test_hard_sets_fallback_exact_boundary() -> None:
    """Exactly 85% of the session best IS hard (>=, inclusive)."""
    best = estimate_1rm(150.0, 5)  # 175.0
    # weight w, 5 reps: w * (1+5/30) = 0.85 * 175 → w = 148.75
    boundary_weight = 0.85 * best / (1 + 5 / 30)
    sets = [
        StrengthSet("squat", 150.0, 5),
        StrengthSet("squat", boundary_weight, 5),
    ]
    summary = session_summary(sets)
    assert summary.hard_sets == 2


# ── summary composition across exercises ──────────────────────────────────


def test_summary_multi_exercise_volume_by_group() -> None:
    sets = [
        StrengthSet("bench press", 100.0, 8),
        StrengthSet("row", 80.0, 10),
        StrengthSet("pull ups", None, 8),  # bodyweight
    ]
    summary = session_summary(sets)
    assert summary.exercises == ["bench press", "row", "pull ups"]
    assert summary.tonnage_kg == pytest.approx(800.0 + 800.0)
    assert summary.bodyweight_sets == 1
    assert summary.volume_by_group == {
        "chest": pytest.approx(800.0),
        "back": pytest.approx(800.0),
    }
    # relative intensity: per set e1RM / that exercise's session best,
    # volume-weighted. bench: bench 100x8 e1RM 126.67, best 126.67 → 1.0,
    # volume 800. row: 80x10 e1RM 106.67, best 106.67 → 1.0, volume 800.
    # bodyweight contributes nothing (no load).
    assert summary.relative_intensity == pytest.approx(1.0)


def test_summary_relative_intensity_volume_weighted() -> None:
    sets = [
        StrengthSet("squat", 140.0, 5),  # e1RM 163.33, volume 700
        StrengthSet("squat", 100.0, 10),  # e1RM 133.33, volume 1000
    ]
    best = estimate_1rm(140.0, 5)
    ratios = [1.0, estimate_1rm(100.0, 10) / best]
    expected = (1.0 * 700.0 + ratios[1] * 1000.0) / 1700.0
    assert not math.isclose(ratios[1], 1.0)  # the fixture is not degenerate
    summary = session_summary(sets)
    assert summary.relative_intensity == pytest.approx(expected)


def test_summary_empty_sets() -> None:
    summary = session_summary([])
    assert summary.set_count == 0
    assert summary.tonnage_kg == 0.0
    assert summary.hard_sets == 0
    assert summary.bodyweight_sets == 0
    assert summary.relative_intensity is None
    assert summary.volume_by_group == {}
    assert summary.exercises == []
