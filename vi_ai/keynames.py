"""Turning keystrokes into words.

Every key the user presses is spoken back so they can confirm what they typed.
A character read out literally is often ambiguous or silent in a speech engine
("." is usually swallowed), so punctuation and control keys get explicit names.
"""

from __future__ import annotations

# Punctuation and symbols, named the way a screen reader names them.
PUNCTUATION_NAMES: dict[str, str] = {
    " ": "space",
    "!": "exclamation mark",
    '"': "quote",
    "#": "hash",
    "$": "dollar",
    "%": "percent",
    "&": "ampersand",
    "'": "apostrophe",
    "(": "left paren",
    ")": "right paren",
    "*": "star",
    "+": "plus",
    ",": "comma",
    "-": "dash",
    ".": "period",
    "/": "slash",
    ":": "colon",
    ";": "semicolon",
    "<": "less than",
    "=": "equals",
    ">": "greater than",
    "?": "question mark",
    "@": "at",
    "[": "left bracket",
    "\\": "backslash",
    "]": "right bracket",
    "^": "caret",
    "_": "underscore",
    "`": "backtick",
    "{": "left brace",
    "|": "bar",
    "}": "right brace",
    "~": "tilde",
    "\t": "tab",
    "\n": "new line",
}

# Non-printing keys, by Tk keysym.
KEYSYM_NAMES: dict[str, str] = {
    "space": "space",
    "Return": "enter",
    "KP_Enter": "enter",
    "BackSpace": "backspace",
    "Delete": "delete",
    "Tab": "tab",
    "ISO_Left_Tab": "back tab",
    "Escape": "escape",
    "Left": "left arrow",
    "Right": "right arrow",
    "Up": "up arrow",
    "Down": "down arrow",
    "Home": "home",
    "End": "end",
    "Prior": "page up",
    "Next": "page down",
    "Insert": "insert",
    "Caps_Lock": "caps lock",
    "Num_Lock": "num lock",
    "Shift_L": "shift",
    "Shift_R": "shift",
    "Control_L": "control",
    "Control_R": "control",
    "Alt_L": "alt",
    "Alt_R": "alt",
    "Super_L": "super",
    "Super_R": "super",
    "Menu": "menu",
}

# Modifier and lock keys produce no character and are not worth announcing on
# their own; the user hears the result when they press the key they modify.
SILENT_KEYSYMS = frozenset({
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Super_L", "Super_R", "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock",
    "Scroll_Lock", "ISO_Level3_Shift", "Multi_key",
})

# Keypad digits and operators arrive with their own keysyms.
_KEYPAD_NAMES: dict[str, str] = {
    "KP_0": "zero", "KP_1": "one", "KP_2": "two", "KP_3": "three",
    "KP_4": "four", "KP_5": "five", "KP_6": "six", "KP_7": "seven",
    "KP_8": "eight", "KP_9": "nine",
    "KP_Decimal": "period", "KP_Add": "plus", "KP_Subtract": "dash",
    "KP_Multiply": "star", "KP_Divide": "slash",
}

# Digits are clearer as words: some engines run "5" into surrounding sounds.
_DIGIT_NAMES = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


def name_character(char: str) -> str:
    """The spoken name of a single printable character.

    Letters are spoken as themselves, with capitals marked so the user can tell
    "a" from "A".
    """
    if not char:
        return ""
    if char in PUNCTUATION_NAMES:
        return PUNCTUATION_NAMES[char]
    if char in _DIGIT_NAMES:
        return _DIGIT_NAMES[char]
    if char.isupper():
        return f"cap {char.lower()}"
    if char.isprintable():
        return char
    return "unknown"


def name_key(keysym: str, char: str = "") -> str:
    """The spoken name for a keystroke.

    `keysym` is the Tk keysym (``event.keysym``) and `char` the character it
    produced (``event.char``), which is empty for keys like F1 or Home. The
    character wins when there is one, so a layout that maps a key to an unusual
    symbol is still announced correctly.
    """
    if keysym in SILENT_KEYSYMS:
        return ""
    if keysym in _KEYPAD_NAMES:
        return _KEYPAD_NAMES[keysym]
    if char and char.isprintable() and char != "\x00":
        return name_character(char)
    if keysym in KEYSYM_NAMES:
        return KEYSYM_NAMES[keysym]
    if char in PUNCTUATION_NAMES:  # tab, newline: printable() is False
        return PUNCTUATION_NAMES[char]
    if len(keysym) == 1:
        return name_character(keysym)
    if keysym.startswith("F") and keysym[1:].isdigit():
        return f"F {keysym[1:]}"
    # Fall back to the keysym itself with separators softened for speech.
    return keysym.replace("_", " ").lower()

