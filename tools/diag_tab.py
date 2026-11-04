"""Diagnostic: which LilyPond inputs make string requests fail on this platform."""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from realbook.engraver import lilypond_exe  # noqa: E402

CASES = {
    "plain": r"{ c4\5 d4\5 es4\5 f4\4 }",
    "key-c-minor": r"{ \key c \minor c4\5 d4\5 es4\5 f4\4 }",
    "key-c-major": r"{ \key c \major c4\5 d4\5 es4\5 f4\4 }",
    "partial": r"{ \partial 2. c4\5 d4\5 es4\5 | f1\4 }",

    "high": r"{ c'4\3 d'4\3 e'4\2 f'4\2 }",
}

from realbook.render_ly import FRET_FUNCTION  # noqa: E402

for name, music in list(CASES.items()) + [("own-frets", CASES["plain"])]:
    own = name == "own-frets"
    ly = ('\\version "2.25.12"\n' + (FRET_FUNCTION + "\n" if own else "") +
          '#(display (ly:version)) #(newline)\n'
          f'm = {music}\n'
          '\\score { \\new TabStaff \\with { stringTunings = \\stringTuning <e, a, d g b e\'> '
          + ("noteToFretFunction = #rb-note-to-fret " if own else "") + '} '
          '{ \\applyContext #(lambda (c) (display (ly:context-property c \'stringTunings)) (newline)) \\m } '
          '\\layout { } }\n')
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "t.ly"
        src.write_text(ly, encoding="utf-8")
        r = subprocess.run([lilypond_exe(), "-dno-print-pages", "-o", str(Path(td) / "t"), str(src)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        warns = [l for l in out.splitlines() if "negative fret" in l]
        tun = [l for l in out.splitlines() if l.startswith("(#<Pitch")]
        print(f"{name:12} warnings={len(warns)} tuning={tun[:1]}")
