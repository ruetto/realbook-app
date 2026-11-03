"""Text formats the review screen uses for editing one bar, with validation."""
from __future__ import annotations

import re

from ..chords import ChordParseError, parse_chord
from ..song import _note

DEFAULT_BEATS = {1: [0], 2: [0, 2], 3: [0, 2, 3], 4: [0, 1, 2, 3]}

CHORD_HELP = ("Chords in the bar, separated by spaces, e.g.  A-7b5 D7b9\n"
              "One chord sits on beat 1; two chords on beats 1 and 3.\n"
              "For other beats add @ and the beat:  G-7@1 C7@4\n"
              "Real Book style: - for minor, maj7, 6, 7b9, -7b5 (or ø), dim7 (or °7), sus4, /bass.")
MELODY_HELP = ("Notes in order: duration then pitch.\n"
               "Durations: 1 whole, 2 half, 2. dotted half, 4 quarter, 4. dotted quarter, 8 eighth, 16 sixteenth.\n"
               "Pitch: letter, b or #, octave (C4 = middle C, the C below the treble staff).\n"
               "Rest: r.  Tie into the next note: ~ at the end.\n"
               "Example:  4r 4C4 4C4 4C4   or   2.Eb4 4Eb4")


def chords_to_text(chords: list[dict]) -> str:
    beats = [c["beat"] for c in chords]
    if beats == DEFAULT_BEATS.get(len(beats), None):
        return " ".join(c["symbol"] for c in chords)
    return " ".join(f"{c['symbol']}@{c['beat'] + 1}" for c in chords)


def text_to_chords(text: str, beats_per_bar: int = 4) -> list[dict]:
    toks = text.split()
    if not toks:
        return []
    explicit = any("@" in t for t in toks)
    out = []
    for i, t in enumerate(toks):
        if "@" in t:
            sym, b = t.rsplit("@", 1)
            if not b.isdigit() or not 1 <= int(b) <= beats_per_bar:
                raise ValueError(f"'{t}': the beat after @ must be 1 to {beats_per_bar}")
            beat = int(b) - 1
        elif explicit:
            raise ValueError("when one chord has @beat, give every chord in the bar a beat")
        else:
            if len(toks) > 4:
                raise ValueError("more than four chords in a bar: use @beat to place them")
            sym, beat = t, DEFAULT_BEATS[len(toks)][i]
        try:
            parse_chord(sym)
        except ChordParseError as e:
            raise ValueError(f"'{sym}' is not a chord I can read ({e})") from None
        out.append({"beat": beat, "symbol": sym})
    beats = [c["beat"] for c in out]
    if len(set(beats)) != len(beats):
        raise ValueError("two chords on the same beat")
    return sorted(out, key=lambda c: c["beat"])


def check_melody(text: str) -> tuple[list[str], float]:
    toks = text.split()
    total = 0.0
    for t in toks:
        try:
            n = _note(t)
        except ValueError:
            raise ValueError(f"'{t}' is not a note. {MELODY_HELP.splitlines()[0]}") from None
        total += n.beats
    return toks, total


def recheck_bar(bar: dict, beats_per_bar: float, is_last: bool) -> None:
    """After an edit, the person has looked at this bar: keep only problems that are still true."""
    total = sum(_note(t).beats for t in bar["notes"])
    bar["doubts"] = []
    if abs(total - beats_per_bar) > 1e-6 and not is_last:
        bar["doubts"].append(f"notes add up to {total:g} beats, not {beats_per_bar:g}")
    if not bar["notes"]:
        bar["doubts"].append("no melody")


def beats_per_bar(song: dict) -> float:
    m = re.match(r"(\d+)/(\d+)", song.get("time", "4/4"))
    return int(m.group(1)) * 4 / int(m.group(2)) if m else 4.0
