"""The chord shapes are the ones jazz guitarists play: a fixed vocabulary of standard grips."""
import pytest

from realbook.chords import DEGREE_SEMIS, parse_chord
from realbook.grips import _TABLE, C_FRET, GRIPS, STANDARD
from realbook.render_ly import arrange
from realbook.song import load
from realbook.voicings import STYLES, TUNINGS, _is_grip, candidates

from pathlib import Path

FIX = Path(__file__).parent / "fixtures"


def _shape(v):
    return " ".join("x" if f is None else str(f) for f in v.frets)


@pytest.mark.parametrize("row,g", list(zip(_TABLE, GRIPS)), ids=[f"{r[3]}:{r[0]}" for r in _TABLE])
def test_grip_notes_match_their_labels(row, g):
    frets = [None if x == "x" else int(x) for x in row[0].split()]
    assert (STANDARD[g.root_string] + frets[g.root_string]) % 12 == 0  # written in C
    assert frets[g.root_string] == C_FRET[g.root_string]
    for s, (f, d) in enumerate(zip(frets, g.degrees)):
        if f is not None:
            assert (STANDARD[s] + f) % 12 == DEGREE_SEMIS[d] % 12, (row, s, d)
    fr = [f for f in frets if f]
    assert max(fr) - min(fr) <= 3


# textbook shapes that must be offered for these symbols (standard tuning, drop-3 style)
KNOWN = {
    "Cmaj7": ["8 x 9 9 8 x", "x 3 x 4 5 3"], "C7": ["8 x 8 9 8 x", "x 3 x 3 5 3"], "C-7": ["8 x 8 8 8 x"],
    "C-7b5": ["8 x 8 8 7 x", "x 3 4 3 4 x"], "Cdim7": ["8 x 7 8 7 x"], "C6": ["8 x 7 9 8 x"],
    "C9": ["x 3 2 3 3 3"], "Cmaj9": ["x 3 2 4 3 x"], "C-9": ["x 3 1 3 3 x"], "C13": ["8 x 8 9 10 x"],
    "C7b9": ["x 3 2 3 2 x"], "C7#9": ["x 3 2 3 4 x"], "C7alt": ["x 3 2 3 4 4"], "C69": ["x 3 2 2 3 3"],
    "C-11": ["x 3 3 3 4 x"], "C9sus4": ["x 3 3 3 3 3"],
}


@pytest.mark.parametrize("sym", list(KNOWN))
def test_standard_shapes_are_offered(sym):
    shapes = {_shape(v) for v in candidates(parse_chord(sym), "drop3", TUNINGS["standard"])}
    for want in KNOWN[sym]:
        assert want in shapes, (sym, want, sorted(shapes)[:8])


@pytest.mark.parametrize("fixture", ["test_tune.json", "minor_test.json"])
@pytest.mark.parametrize("key", ["C", "Eb", "E", "G", "Bb", "F#"])
def test_voicings_come_from_the_vocabulary_and_fit_the_hand(fixture, key):
    s = load(FIX / fixture).transposed(key)
    for style in STYLES:
        arr = arrange(s, style, "standard")
        for _, ch, v in arr.voicings:
            fr = [f for f in v.frets if f]
            assert max(fr) - min(fr) <= 3, (style, ch.symbol, _shape(v))
            if style in ("drop3", "shell") and ("seventh" in ch.tones or "sixth" in ch.tones):
                assert _is_grip(ch, style, v, TUNINGS["standard"]), (style, ch.symbol, _shape(v))


@pytest.mark.parametrize("fixture", ["test_tune.json", "minor_test.json"])
@pytest.mark.parametrize("key", ["C", "Db", "E", "G", "Bb"])
def test_comping_shapes_are_small(fixture, key):
    """Shells: root, 3rd, 7th (m7b5/dim7 add their 5th). Rootless: three notes. Hands stay in one area."""
    s = load(FIX / fixture).transposed(key)
    for style in ("shell", "rootless"):
        arr = arrange(s, style, "standard")
        prev = None
        for _, ch, v in arr.voicings:
            n = sum(f is not None for f in v.frets)
            limit = 4 if style == "shell" and ch.family() in ("hdim", "dim") else 3
            assert n <= limit, (style, ch.symbol, _shape(v))
            assert v.span <= 3 and (v.span <= 2 or style == "rootless"), (style, ch.symbol, _shape(v))
            if style == "shell" and ("seventh" in ch.tones or "sixth" in ch.tones):
                assert v.degrees[[i for i, f in enumerate(v.frets) if f is not None][0]] == "1"
            if prev is not None:
                assert abs(v.avg_fret - prev.avg_fret) <= 6, (style, ch.symbol)
            prev = v


def test_shells_are_the_textbook_ones():
    from realbook.chords import parse_chord
    for sym, want in {"Cmaj7": "x 3 2 4 x x", "C7": "8 x 8 9 x x", "C-7": "x 3 1 3 x x", "C6": "8 x 7 9 x x",
                      "C-7b5": "8 x 8 8 7 x", "C7b9": "x 3 2 3 x x", "C13": "8 x 8 9 x x"}.items():
        assert want in {_shape(v) for v in candidates(parse_chord(sym), "shell", TUNINGS["standard"])}, sym
