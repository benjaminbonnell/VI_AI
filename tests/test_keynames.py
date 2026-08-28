"""Every keystroke is announced, so these names are user-facing."""

import pytest

from vi_ai.keynames import name_character, name_key


@pytest.mark.parametrize(
    "keysym,char,expected",
    [
        ("a", "a", "a"),
        ("A", "A", "cap a"),
        ("space", " ", "space"),
        ("period", ".", "period"),
        ("comma", ",", "comma"),
        ("question", "?", "question mark"),
        ("Return", "\r", "enter"),
        ("KP_Enter", "\r", "enter"),
        ("BackSpace", "\x08", "backspace"),
        ("Escape", "\x1b", "escape"),
        ("F1", "", "F 1"),
        ("F12", "", "F 12"),
        ("Left", "", "left arrow"),
        ("KP_7", "7", "seven"),
        ("5", "5", "five"),
    ],
)
def test_key_names(keysym, char, expected):
    assert name_key(keysym, char) == expected


@pytest.mark.parametrize("keysym", ["Shift_L", "Control_R", "Alt_L", "Caps_Lock"])
def test_modifier_keys_are_silent(keysym):
    """Announcing shift on its own would double the noise of typing a capital."""
    assert name_key(keysym, "") == ""


def test_unknown_keysym_falls_back_to_a_readable_form():
    assert name_key("XF86AudioRaiseVolume", "") == "xf86audioraisevolume"
    assert name_key("Page_Up", "") == "page up"


def test_capitals_are_distinguishable_from_lowercase():
    assert name_character("q") != name_character("Q")


def test_every_ascii_printable_has_a_name():
    for code in range(32, 127):
        assert name_character(chr(code)), f"no name for {chr(code)!r}"
