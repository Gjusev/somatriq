"""Parser determinism (spec §103: do not invent quantity; ADR 0009).

The parser is pure string rules — same text in, same payload out, forever.
The critical property: a caffeine event carries a quantity ONLY when a
number is literally present; "coffee" alone never gains one.
"""

from somatriq_telegram.parser import parse_quick_log


def test_coffee_without_number_never_invents_quantity() -> None:
    parsed = parse_quick_log("I drank a coffee now")
    assert parsed.kind == "caffeine"
    assert parsed.structured == {"estimated": False}
    assert "quantity" not in (parsed.structured or {})


def test_coffee_with_literal_number() -> None:
    parsed = parse_quick_log("coffee 2")
    assert parsed.kind == "caffeine"
    assert parsed.structured == {"quantity": 2}


def test_kaffee_with_decimal_number_comma() -> None:
    parsed = parse_quick_log("Kaffee 1,5")
    assert parsed.kind == "caffeine"
    assert parsed.structured == {"quantity": 1.5}


def test_cafe_accented_and_case_insensitive() -> None:
    for text in ("Cafe", "café", "COFFEE", "ein Kaffee gerade"):
        parsed = parse_quick_log(text)
        assert parsed.kind == "caffeine", text
        assert parsed.structured == {"estimated": False}, text


def test_number_attached_to_unit() -> None:
    parsed = parse_quick_log("coffee 200mg")
    assert parsed.kind == "caffeine"
    assert parsed.structured == {"quantity": 200}


def test_everything_else_is_a_journal_note() -> None:
    parsed = parse_quick_log("felt great after the run")
    assert parsed.kind == "journal"
    assert parsed.structured is None
    assert parsed.reply == "Logged: journal note"


def test_parser_is_deterministic() -> None:
    for text in ("coffee", "coffee 2", "tired today", "Kaffee"):
        assert parse_quick_log(text) == parse_quick_log(text)


def test_empty_argument_is_a_journal_note() -> None:
    """Degenerate input still maps to the journal branch (never crashes)."""
    parsed = parse_quick_log("")
    assert parsed.kind == "journal"
