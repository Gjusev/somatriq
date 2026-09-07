"""Strength training: deterministic parser + load math (spec §78-80, §104,
§205 M12; ADR 0009 — deterministic Python, never an LLM).

Spec §104 allows AI-assisted parsing but demands the final representation be
deterministic and editable — this module IS the deterministic
representation: a pure string grammar over one line of text, plus the load
mathematics computed from it. Nothing here touches the DB; the Telegram bot
and the API persist what these functions produce.

Parser grammar (one exercise per line, spec §104 example)::

    Chest press 180x8 170x9 160x10
    <exercise words> <weight>x<reps> [<weight>x<reps> ...] [rir=N] [rpe=N]

* ``x`` separators: ``x``, ``X`` or ``×`` (``180x8`` / ``180X8`` / ``180×8``).
* ``rir=N`` / ``rpe=N`` (case-insensitive) may follow a set token (that
  set) or appear once after the final set token with no other rir/rpe
  tokens (then it applies to EVERY set of the line).
* Bodyweight form — ``pull-ups 8 8 7``: TWO OR MORE bare integers after the
  exercise words are sets with no external load (``weight_kg=None``). A
  single bare integer is NOT a rep pattern (``coffee 2`` stays a caffeine
  quantity, spec §103); bare integers mixed with weighted groups make the
  line not-a-training-line rather than an ambiguous guess.
* A line with no weighted group and no bare-rep pattern returns ``None`` —
  the caller (the /log router) falls through to journal/caffeine handling.

Mathematics (documented, frozen — changes bump this docstring):

* ``estimate_1rm`` — EPLEY with RIR-adjusted reps:
  ``e1RM = weight * (1 + effective_reps / 30)`` where
  ``effective_reps = reps + rir`` when RIR is stated (a set ended 2 reps
  early could have produced ~2 more reps; the estimate uses the reps the
  lifter could have done). Bodyweight sets return ``0.0`` — there is no
  external load to estimate from, and none is invented.
* ``hard sets`` — a set is hard when RIR <= 2 OR RPE >= 8. FALLBACK when
  NEITHER RIR nor RPE was recorded: the set's e1RM is >= 85% of the
  session's BEST e1RM for that same exercise (per-exercise, never the
  global session best — a row set is not heavy because a squat was). A set
  with an explicit RIR > 2 / RPE < 8 is NOT re-judged by the fallback: the
  lifter said reps were left in reserve. A bodyweight set without RIR/RPE
  is never counted hard — no load exists to reason about.
* ``relative_intensity`` — the volume-weighted mean of
  ``set e1RM / session-best e1RM`` (per exercise) over the weighted sets,
  weights being each set's tonnage (weight × reps): the sets doing more of
  the session's work represent more of its intensity. ``None`` when the
  session has no weighted sets.
* ``tonnage`` — Σ weight × reps over weighted sets; bodyweight sets
  contribute 0 and are counted separately (``bodyweight_sets``).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

# ── exercise catalog (spec §79 muscle groups; ~16 common lifts) ───────────
# exercise → [PRIMARY muscle group, secondary...]; the primary is what a
# training_sets.muscle_group stores. The catalog never guesses: an exercise
# that resolves to none of these is muscle_group 'other'.

EXERCISE_CATALOG: Final[dict[str, list[str]]] = {
    "squat": ["quads"],
    "front squat": ["quads"],
    "lunge": ["quads"],
    "deadlift": ["posterior_chain"],
    "rdl": ["posterior_chain"],
    "hip thrust": ["posterior_chain"],
    "bench press": ["chest"],
    "incline bench": ["chest"],
    "chest press": ["chest"],
    "dip": ["chest", "triceps"],  # chest/triceps — one primary: chest
    "ohp": ["shoulders"],
    "military press": ["shoulders"],
    "lateral raise": ["shoulders"],
    "row": ["back"],
    "pull up": ["back"],
    "lat pulldown": ["back"],
    "biceps curl": ["arms"],
    "triceps extension": ["arms"],
    "calf raise": ["calves"],
    "plank": ["core"],
}

# Surface variants → catalog key (matched exactly after normalization).
_NAME_ALIASES: Final[dict[str, str]] = {
    "overhead press": "ohp",
    "press": "ohp",
    "bench": "bench press",
    "incline press": "incline bench",
    "pull ups": "pull up",
    "pullup": "pull up",
    "chin up": "pull up",
    "chin ups": "pull up",
    "pulldown": "lat pulldown",
    "pull down": "lat pulldown",
    "curl": "biceps curl",
    "curls": "biceps curl",
    "hammer curl": "biceps curl",
    "hammer curls": "biceps curl",
    "triceps extensions": "triceps extension",
    "squats": "squat",
    "deadlifts": "deadlift",
    "lunges": "lunge",
    "dips": "dip",
    "calf raises": "calf raise",
    "hip thrusts": "hip thrust",
    "planks": "plank",
}

OTHER_GROUP: Final = "other"

# Hard-set rule constants (see module docstring).
_HARD_RIR_MAX: Final = 2
_HARD_RPE_MIN: Final = 8.0
_HARD_E1RM_FRACTION: Final = 0.85

# ── parser ────────────────────────────────────────────────────────────────

_WEIGHTED_RE: Final = re.compile(r"^(\d+(?:[.,]\d+)?)\s*[xX×]\s*(\d+)$")
_BARE_REPS_RE: Final = re.compile(r"^\d+$")
_RIR_RE: Final = re.compile(r"^rir\s*=\s*(\d+)$", re.IGNORECASE)
_RPE_RE: Final = re.compile(r"^rpe\s*=\s*(\d+(?:[.,]\d+)?)$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedSet:
    """One parsed set; ``weight_kg=None`` means bodyweight (no load)."""

    weight_kg: float | None
    reps: int
    rir: int | None = None
    rpe: float | None = None


@dataclass(frozen=True)
class ParsedTraining:
    """The deterministic representation of one training line (spec §104)."""

    exercise: str  # normalized lowercase surface name (hyphens → spaces)
    sets: list[ParsedSet]

    def to_strength_sets(self) -> list["StrengthSet"]:
        """The same sets as summary input records (single exercise)."""
        return [StrengthSet(self.exercise, s.weight_kg, s.reps, s.rir, s.rpe) for s in self.sets]


@dataclass(frozen=True)
class StrengthSet:
    """One stored set as summary input (exercise travels with the set so a
    session summary can span exercises)."""

    exercise: str
    weight_kg: float | None
    reps: int
    rir: int | None = None
    rpe: float | None = None


@dataclass(frozen=True)
class StrengthSummary:
    """The §79 session calculations, all deterministic."""

    set_count: int
    tonnage_kg: float
    hard_sets: int
    bodyweight_sets: int
    relative_intensity: float | None  # None when no weighted sets
    volume_by_group: dict[str, float]
    best_e1rm_by_exercise: dict[str, float]
    exercises: list[str]  # distinct, first-appearance order


def normalize_exercise_name(name: str) -> str:
    """Lowercase; hyphens/underscores → single spaces; trimmed."""
    return re.sub(r"[\s\-_]+", " ", name.lower()).strip()


def muscle_group_for(exercise: str) -> str:
    """Primary muscle group from the catalog; 'other' when unknown.

    Resolution order: exact alias → exact catalog key → the catalog key that
    appears inside the name on word boundaries (earliest match wins, then
    the longest — "incline bench press" is incline bench, not bench press).
    'other' is never a guess beyond the catalog.
    """
    name = normalize_exercise_name(exercise)
    if not name:
        return OTHER_GROUP
    alias = _NAME_ALIASES.get(name)
    if alias is not None:
        return EXERCISE_CATALOG[alias][0]
    if name in EXERCISE_CATALOG:
        return EXERCISE_CATALOG[name][0]
    best: tuple[int, str] | None = None  # (position, key) — position asc, len desc
    for key in EXERCISE_CATALOG:
        match = re.search(rf"\b{re.escape(key)}\b", name)
        if match is None:
            continue
        candidate = (match.start(), key)
        if (
            best is None
            or candidate[0] < best[0]
            or (candidate[0] == best[0] and len(candidate[1]) > len(best[1]))
        ):
            best = candidate
    if best is not None:
        return EXERCISE_CATALOG[best[1]][0]
    return OTHER_GROUP


def parse_training_line(text: str) -> ParsedTraining | None:
    """Parse one §104 training line; ``None`` when the text is not one.

    ``None`` is the router signal: the line falls through to the journal /
    caffeine handling unchanged (spec §103 behavior is preserved — e.g.
    "I drank a coffee now" and "coffee 2" are never training).
    """
    tokens = [t.strip(",;.") for t in text.split()]
    if not tokens:
        return None

    exercise_words: list[str] = []
    weighted: list[tuple[float, int]] = []
    bare_reps: list[int] = []
    # rir/rpe tokens: (kind, value, token_index, set_index_so_far)
    effort: list[tuple[str, float, int, int]] = []
    sets_started = False

    for index, token in enumerate(tokens):
        if not token:
            continue
        rir = _RIR_RE.match(token)
        if rir is not None:
            effort.append(("rir", float(rir.group(1)), index, len(weighted) + len(bare_reps)))
            sets_started = sets_started or bool(weighted or bare_reps)
            continue
        rpe = _RPE_RE.match(token)
        if rpe is not None:
            value = float(rpe.group(1).replace(",", "."))
            effort.append(("rpe", value, index, len(weighted) + len(bare_reps)))
            sets_started = sets_started or bool(weighted or bare_reps)
            continue
        weighted_match = _WEIGHTED_RE.match(token)
        if weighted_match is not None:
            weight = float(weighted_match.group(1).replace(",", "."))
            reps = int(weighted_match.group(2))
            if weight <= 0 or reps <= 0:
                return None
            weighted.append((weight, reps))
            sets_started = True
            continue
        if _BARE_REPS_RE.match(token):
            reps = int(token)
            if reps <= 0:
                return None
            bare_reps.append(reps)
            sets_started = True
            continue
        if sets_started:
            # A plain word after the sets began is not in the grammar —
            # refuse rather than guess what it meant.
            return None
        exercise_words.append(token)

    if not exercise_words:
        return None
    if weighted and bare_reps:
        return None  # ambiguous mix — never guess
    if not weighted and len(bare_reps) < 2:
        return None  # a single bare number is a quantity, not a rep pattern

    exercise = normalize_exercise_name(" ".join(exercise_words))

    if weighted:
        raw_sets = [ParsedSet(w, r) for w, r in weighted]
    else:
        raw_sets = [ParsedSet(None, r) for r in bare_reps]

    # Effort attachment: when every rir/rpe token sits AFTER the last set
    # token and there is at most one of each kind, the values describe the
    # whole line (the "once at the end" form); otherwise each token belongs
    # to the most recent set that existed when it appeared.
    last_set_token_index = max(
        index
        for index, token in enumerate(tokens)
        if token
        and (
            _WEIGHTED_RE.match(token) is not None
            or (_BARE_REPS_RE.match(token) is not None and (weighted or len(bare_reps) >= 2))
        )
    )
    kinds = [kind for kind, _, _, _ in effort]
    at_end = all(token_index > last_set_token_index for _, _, token_index, _ in effort)
    one_each = kinds.count("rir") <= 1 and kinds.count("rpe") <= 1
    sets = list(raw_sets)
    if effort and at_end and one_each:
        for kind, value, _, _ in effort:
            for i, current in enumerate(sets):
                if kind == "rir" and current.rir is None:
                    sets[i] = ParsedSet(current.weight_kg, current.reps, int(value), current.rpe)
                elif kind == "rpe" and current.rpe is None:
                    sets[i] = ParsedSet(current.weight_kg, current.reps, current.rir, value)
    else:
        for kind, value, _, seen_sets in effort:
            if seen_sets == 0:
                continue  # an effort token before any set belongs to nothing
            i = seen_sets - 1
            current = sets[i]
            if kind == "rir" and current.rir is None:
                sets[i] = ParsedSet(current.weight_kg, current.reps, int(value), current.rpe)
            elif kind == "rpe" and current.rpe is None:
                sets[i] = ParsedSet(current.weight_kg, current.reps, current.rir, value)

    return ParsedTraining(exercise=exercise, sets=sets)


# ── mathematics ───────────────────────────────────────────────────────────


def estimate_1rm(weight: float | None, reps: int, rir: int | None = None) -> float:
    """Epley e1RM with RIR-adjusted reps (see module docstring).

    Bodyweight (``weight is None``) → 0.0: no external load exists to
    estimate a maximum from, and none is invented.
    """
    if weight is None:
        return 0.0
    effective_reps = reps + (rir or 0)
    return weight * (1.0 + effective_reps / 30.0)


def _is_hard(set_: StrengthSet, best_e1rm: float | None) -> bool:
    """RIR <= 2 OR RPE >= 8; the 85%-of-session-best fallback applies ONLY
    when neither was recorded (an explicit easy effort is never re-judged)."""
    if set_.rir is not None and set_.rir <= _HARD_RIR_MAX:
        return True
    if set_.rpe is not None and set_.rpe >= _HARD_RPE_MIN:
        return True
    if set_.rir is not None or set_.rpe is not None:
        return False
    # Fallback (neither RIR nor RPE recorded): >= 85% of the session's best
    # e1RM FOR THIS EXERCISE. Bodyweight sets have no load → never hard.
    if set_.weight_kg is None or best_e1rm is None or best_e1rm <= 0.0:
        return False
    return estimate_1rm(set_.weight_kg, set_.reps, set_.rir) >= _HARD_E1RM_FRACTION * best_e1rm


def session_summary(sets: Sequence[StrengthSet]) -> StrengthSummary:
    """The §79 session calculations over one session's sets."""
    tonnage = 0.0
    bodyweight_sets = 0
    hard_sets = 0
    volume_by_group: dict[str, float] = {}
    best_e1rm: dict[str, float] = {}
    exercises: list[str] = []

    for entry in sets:
        if entry.exercise not in best_e1rm:
            exercises.append(entry.exercise)
            best_e1rm[entry.exercise] = 0.0
            volume_by_group.setdefault(muscle_group_for(entry.exercise), 0.0)
        if entry.weight_kg is None:
            bodyweight_sets += 1
            continue
        tonnage += entry.weight_kg * entry.reps
        volume_by_group[muscle_group_for(entry.exercise)] += entry.weight_kg * entry.reps
        e1rm = estimate_1rm(entry.weight_kg, entry.reps, entry.rir)
        if e1rm > best_e1rm[entry.exercise]:
            best_e1rm[entry.exercise] = e1rm

    weighted_ratio_sum = 0.0
    weighted_volume = 0.0
    for entry in sets:
        if entry.weight_kg is None:
            continue
        best = best_e1rm[entry.exercise]
        if best > 0.0:
            volume = entry.weight_kg * entry.reps
            ratio = estimate_1rm(entry.weight_kg, entry.reps, entry.rir) / best
            weighted_ratio_sum += ratio * volume
            weighted_volume += volume

    for entry in sets:
        if _is_hard(entry, best_e1rm.get(entry.exercise)):
            hard_sets += 1

    return StrengthSummary(
        set_count=len(sets),
        tonnage_kg=tonnage,
        hard_sets=hard_sets,
        bodyweight_sets=bodyweight_sets,
        relative_intensity=(
            weighted_ratio_sum / weighted_volume if weighted_volume > 0.0 else None
        ),
        volume_by_group=volume_by_group,
        best_e1rm_by_exercise=best_e1rm,
        exercises=exercises,
    )
