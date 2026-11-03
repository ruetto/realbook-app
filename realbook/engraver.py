"""Locate the LilyPond engraver: bundled next to the app when packaged, else from the 'lilypond' pip package."""
from __future__ import annotations

import sys
from pathlib import Path


def lilypond_exe() -> str:
    exe = "lilypond.exe" if sys.platform == "win32" else "lilypond"
    if getattr(sys, "frozen", False):
        p = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "lilypond-binaries" / "bin" / exe
        if p.exists():
            return str(p)
    import lilypond

    p = Path(lilypond.executable())
    if sys.platform == "win32" and not p.suffix:
        p = p.with_suffix(".exe")
    return str(p)
