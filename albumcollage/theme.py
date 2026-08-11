"""Dark and light palettes, applied live without restarting the app."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

THEMES = ("dark", "light", "system")

_DARK = {
    "window": "#1e1e20",
    "base": "#161618",
    "alt": "#2a2a2e",
    "text": "#e4e4e8",
    "disabled": "#7a7a80",
    "highlight": "#4078c8",
    "highlight_text": "#ffffff",
    "border": "#3a3a40",
    "placeholder": "#2b2b2b",
}

_LIGHT = {
    "window": "#f4f4f6",
    "base": "#ffffff",
    "alt": "#e8e8ec",
    "text": "#1b1b1f",
    "disabled": "#9a9aa0",
    "highlight": "#3874c8",
    "highlight_text": "#ffffff",
    "border": "#c8c8d0",
    "placeholder": "#dcdce2",
}


def resolve(name: str) -> str:
    """Turn 'system' into a concrete theme; anything unknown becomes 'dark'."""
    if name == "system":
        try:
            hints = QApplication.styleHints()
            scheme = hints.colorScheme()
            return "light" if scheme == Qt.ColorScheme.Light else "dark"
        except (AttributeError, TypeError):
            return "dark"       # Qt < 6.5 has no colour-scheme hint
    return name if name in ("dark", "light") else "dark"


_current = "dark"


def current() -> str:
    """Theme name as last applied (before 'system' is resolved)."""
    return _current


def colours(name: str | None = None) -> dict:
    """Colour dictionary for a theme, or for whatever is currently applied."""
    return dict(_LIGHT if resolve(name or _current) == "light" else _DARK)


def _palette(spec: dict) -> QPalette:
    p = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    window = QColor(spec["window"])
    base = QColor(spec["base"])
    alt = QColor(spec["alt"])
    text = QColor(spec["text"])
    disabled = QColor(spec["disabled"])

    p.setColor(role.Window, window)
    p.setColor(role.WindowText, text)
    p.setColor(role.Base, base)
    p.setColor(role.AlternateBase, alt)
    p.setColor(role.ToolTipBase, alt)
    p.setColor(role.ToolTipText, text)
    p.setColor(role.Text, text)
    p.setColor(role.Button, alt)
    p.setColor(role.ButtonText, text)
    p.setColor(role.BrightText, QColor("#ff5555"))
    p.setColor(role.Link, QColor(spec["highlight"]))
    p.setColor(role.Highlight, QColor(spec["highlight"]))
    p.setColor(role.HighlightedText, QColor(spec["highlight_text"]))
    p.setColor(role.Mid, QColor(spec["border"]))
    p.setColor(role.PlaceholderText, disabled)

    for r in (role.WindowText, role.Text, role.ButtonText):
        p.setColor(group.Disabled, r, disabled)
    return p


def apply_theme(app: QApplication, name: str) -> str:
    """Apply a theme to the running application and return the resolved name."""
    global _current
    resolved = resolve(name)
    app.setStyle("Fusion")
    app.setPalette(_palette(_LIGHT if resolved == "light" else _DARK))
    _current = name
    return resolved
