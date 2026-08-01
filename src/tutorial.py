"""Minimal tutorial stubs for local playtest / watch page.

The hosted competition ships richer walkthrough assets; competitors only need
these symbols so `src.watch` can import cleanly for `scripts/playtest.py`.
"""

TUTORIAL_CSS = "/* tutorial disabled in competitor checkout */"
TUTORIAL_HTML = ""


def tutorial_js(_mode: str = "agent") -> str:
    return """
window.tutorialStop = window.tutorialStop || function () {};
window.tutorialMaybeStart = window.tutorialMaybeStart || function () {};
window.tutorialWillOpen = window.tutorialWillOpen || function () {};
window.tutorialWillClose = window.tutorialWillClose || function () {};
window.tutorialDidClose = window.tutorialDidClose || function () {};
"""
