"""Chord naming used by the OpenBook converter: pitches as LilyPond's chordmode produces them -> Real Book name."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools" / "openbook"))
from convert import Pitch, name_chord  # noqa: E402

from realbook.chords import parse_chord  # noqa: E402

N = {"c": 0, "d": 1, "e": 2, "f": 3, "g": 4, "a": 5, "b": 6}


def P(spec):
    """'ees0' -> E-flat in LilyPond octave 0; 'fis1' -> F# an octave up."""
    name, octv = spec[:-1], int(spec[-1]) if spec[-1].isdigit() else 0
    alt = 0
    if name.endswith("eses") and len(name) > 4:
        alt, name = -1, name[:-4]
    elif name.endswith("isis"):
        alt, name = 1, name[:-4]
    elif name.endswith("es") and len(name) > 2:
        alt, name = -0.5, name[:-2]
    elif name in ("as", "es"):
        alt, name = -0.5, name[0]
    elif name.endswith("is"):
        alt, name = 0.5, name[:-2]
    from fractions import Fraction
    return Pitch(N[name], Fraction(alt).limit_denominator(2), octv)


def ev(specs, inv=None, bass=None):
    out = [{"pitch": P(s), "inv": s == inv, "bass": s == bass} for s in specs]
    return out


@pytest.mark.parametrize("specs,inv,bass,want", [
    (["c0", "e0", "g0", "bes0"], None, None, "C7"),
    (["a0", "c1", "e1", "g1"], None, None, "A-7"),
    (["fis0", "a0", "c1", "e1"], None, None, "F#-7b5"),
    (["b0", "dis1", "fis1", "a1", "c2"], None, None, "B7b9"),
    (["d0", "fis0", "a0", "c1", "ees1"], None, None, "D7b9"),
    (["g0", "b0", "dis1", "f1", "ais1"], None, None, "G+7#9"),
    (["c0", "ees0", "ges0", "beses0"], None, None, "Cdim7"),
    (["f0", "a0", "c1", "d1"], None, None, "F6"),
    (["bes0", "des1", "f1", "g1"], None, None, "Bb-6"),
    (["c0", "e0", "g0", "b0", "d1"], None, None, "Cmaj9"),
    (["g0", "c1", "d1", "f1"], None, None, "G7sus4"),
    # inversions as chordmode lays them out: the bass tone moved down, other tones possibly moved up
    (["c0", "ees0", "aes0"], "c0", None, "Ab/C"),
    (["d0", "g0", "b0", "f1"], "d0", None, "G7/D"),
    (["cis0", "e0", "g0", "a0"], "cis0", None, "A7/C#"),
    # added bass (c/+e style): not a chord tone
    (["d0", "c1", "e1", "g1"], None, "d0", "C/D"),
])
def test_chord_names(specs, inv, bass, want):
    sym, ok, pcs = name_chord(ev(specs, inv, bass))
    assert ok, sym
    assert sym == want
    assert parse_chord(sym).pcs() == pcs
