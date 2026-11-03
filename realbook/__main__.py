"""Command line: python -m realbook SONG.json [--style drop3] [--key Bb] [--tuning standard] [-o out.pdf]"""
import argparse
from pathlib import Path

from .render_ly import arrange, compile_pdf, to_lilypond
from .song import load
from .voicings import STYLES, TUNINGS, verify


def main():
    ap = argparse.ArgumentParser(description="Render a lead sheet with melody, tab and jazz guitar voicings.")
    ap.add_argument("song")
    ap.add_argument("--style", choices=list(STYLES), default="drop3")
    ap.add_argument("--key", default=None, help="target key, e.g. Bb, Eb, F#")
    ap.add_argument("--tuning", choices=list(TUNINGS), default="standard")
    ap.add_argument("--concert", action="store_true", help="melody at concert pitch instead of guitar octave")
    ap.add_argument("--open-strings", choices=["avoid", "allow"], default="avoid")
    ap.add_argument("--position", default="auto", help="'auto' or the fret for the index finger, e.g. 5")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--ly", action="store_true", help="also save the LilyPond source")
    a = ap.parse_args()

    song = load(a.song)
    problems = song.check()
    if problems:
        raise SystemExit("Song file problems:\n  " + "\n  ".join(problems))
    if a.key:
        song = song.transposed(a.key)
    arr = arrange(song, a.style, a.tuning, "concert" if a.concert else "guitar", a.open_strings, a.position)
    bad = [(ch.symbol, p) for _, ch, v in arr.voicings for p in verify(ch, a.style, v, arr.tuning)]
    if bad:
        raise SystemExit(f"Voicing check failed: {bad}")
    ly = to_lilypond(arr)
    out = Path(a.output or f"{Path(a.song).stem}_{song.key}_{a.style}_{a.tuning}.pdf")
    if a.ly:
        out.with_suffix(".ly").write_text(ly)
    compile_pdf(ly, out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
