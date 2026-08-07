"""TUI presentation contracts owned by the Python backend.

The theme names here are the authoritative list. They are mirrored in
``clients/tui/src/ui/theme.ts``, which owns the actual color definitions;
``tests/test_tui_theme.py`` asserts the two lists stay in sync.
"""

from __future__ import annotations

from enum import StrEnum


class TuiTheme(StrEnum):
    """Selectable TUI themes, as light/dark pairs."""

    DARK = "dark"
    LIGHT = "light"
    SOLARIZED_DARK = "solarized-dark"
    SOLARIZED_LIGHT = "solarized-light"
    CATPPUCCIN_MOCHA = "catppuccin-mocha"
    CATPPUCCIN_LATTE = "catppuccin-latte"
    HIGH_CONTRAST_DARK = "high-contrast-dark"
    HIGH_CONTRAST_LIGHT = "high-contrast-light"


DEFAULT_TUI_THEME = TuiTheme.DARK
KNOWN_TUI_THEMES: tuple[str, ...] = tuple(theme.value for theme in TuiTheme)
