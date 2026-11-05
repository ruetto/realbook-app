"""The chord shapes jazz guitarists actually play ("grips"), as a vocabulary for the voicing engine.

Every shape below is a standard movable grip from the common jazz-guitar repertoire, written here in C in
standard tuning (frets low E to high E, x = muted) with the chord degree of each note. The engine moves
them up and down the neck (and into other tunings when the shape stays playable) and picks, for each chord
symbol, the grips that sound the most of what the symbol asks for without adding anything it doesn't:
first the 3rd and 7th, then alterations (b9 #9 b5 #5 #11 b13), then 9 11 13.

A chord with no exact grip gets the nearest one that keeps its guide tones and alterations, as players do
(C7b9#11 -> a 7b9 grip). tests/test_grips.py recomputes every note of every grip from its frets.
"""
from __future__ import annotations

from dataclasses import dataclass

from .chords import DEGREE_SEMIS, Chord

STANDARD = [40, 45, 50, 55, 59, 64]
C_FRET = {0: 8, 1: 3, 2: 10}  # where C is on the 6th, 5th and 4th strings
MAX_SPAN = 3  # frets between the lowest and highest fretted note: a four-fret hand position


@dataclass(frozen=True)
class Grip:
    name: str  # e.g. 'maj7 (6th-string root)'
    sets: frozenset  # styles this grip belongs to: 'drop3', 'shell', 'triad'
    root_string: int  # 0 = low E
    rel: tuple  # per string: semitones above the root note, or None (muted)
    degrees: tuple  # per string: degree label, '' if muted


def _grip(shape: str, degrees: str, name: str, sets: str) -> Grip:
    frets = [None if x == "x" else int(x) for x in shape.split()]
    degs = degrees.split()
    played = [s for s, f in enumerate(frets) if f is not None]
    rs = played[0]
    root_midi = STANDARD[rs] + frets[rs]
    rel, labels, k = [], [], 0
    for s, f in enumerate(frets):
        if f is None:
            rel.append(None)
            labels.append("")
        else:
            rel.append(STANDARD[s] + f - root_midi)
            labels.append(degs[k])
            k += 1
    assert k == len(degs), (shape, degrees)
    return Grip(f"{name} ({['6th', '5th', '4th'][rs]}-string root)", frozenset(sets.split()), rs, tuple(rel),
                tuple(labels))


# shape (in C), degrees low to high, name, styles that use it
_TABLE = [
    # ---- 6th-string root
    ("8 x 9 9 8 x", "1 7 3 5", "maj7", "drop3"),
    ("8 x 7 9 8 x", "1 6 3 5", "6", "drop3"),
    ("8 x 9 9 x 10", "1 7 3 9", "maj9", "drop3"),
    ("8 x 9 9 7 x", "1 7 3 #11", "maj7#11", "drop3"),
    ("8 x 8 9 8 x", "1 b7 3 5", "7", "drop3"),
    ("8 x 8 9 x 10", "1 b7 3 9", "9", "drop3"),
    ("8 x 8 9 10 x", "1 b7 3 13", "13", "drop3"),
    ("8 x 8 9 10 9", "1 b7 3 13 b9", "13b9", "drop3"),
    ("8 x 8 9 x 9", "1 b7 3 b9", "7b9", "drop3"),
    ("8 x 8 9 x 11", "1 b7 3 #9", "7#9", "drop3"),
    ("8 x 8 9 9 x", "1 b7 3 #5", "7#5", "drop3"),
    ("8 x 8 9 9 9", "1 b7 3 #5 b9", "7#5b9", "drop3"),
    ("8 x 8 9 7 x", "1 b7 3 b5", "7b5", "drop3"),
    ("8 x 8 10 8 x", "1 b7 4 5", "7sus4", "drop3"),
    ("8 x 8 10 10 x", "1 b7 4 13", "13sus4", "drop3"),
    ("8 x 8 8 8 x", "1 b7 b3 5", "m7", "drop3"),
    ("8 x 7 8 8 x", "1 6 b3 5", "m6", "drop3"),
    ("8 x 8 8 x 10", "1 b7 b3 9", "m9", "drop3"),
    ("8 x 8 8 6 x", "1 b7 b3 11", "m11", "drop3"),
    ("8 x 9 8 8 x", "1 7 b3 5", "m(maj7)", "drop3"),
    ("8 x 8 8 7 x", "1 b7 b3 b5", "m7b5", "drop3"),
    ("8 x 7 8 7 x", "1 bb7 b3 b5", "dim7", "drop3 shell"),
    # shells: root, 3rd/7th on the 4th and 3rd strings
    ("8 x 9 9 x x", "1 7 3", "maj7 shell", "shell"),
    ("8 x 7 9 x x", "1 6 3", "6 shell", "shell"),
    ("8 x 8 9 x x", "1 b7 3", "7 shell", "shell"),
    ("8 x 8 10 x x", "1 b7 4", "7sus4 shell", "shell"),
    ("8 x 8 8 x x", "1 b7 b3", "m7 shell", "shell"),
    ("8 x 7 8 x x", "1 6 b3", "m6 shell", "shell"),
    ("8 x 9 8 x x", "1 7 b3", "m(maj7) shell", "shell"),
    ("8 x 8 8 7 x", "1 b7 b3 b5", "m7b5", "shell"),
    # triads as root-position barre fragments
    ("8 x 10 9 8 x", "1 1 3 5", "major", "triad drop3"),
    ("8 x 10 8 8 x", "1 1 b3 5", "minor", "triad drop3"),
    ("8 x 10 9 9 x", "1 1 3 #5", "augmented", "triad drop3"),
    ("8 x 10 10 8 x", "1 1 4 5", "sus4", "triad drop3"),
    # ---- 5th-string root
    ("x 3 x 4 5 3", "1 7 3 5", "maj7", "drop3"),
    ("x 3 5 4 5 x", "1 5 7 3", "maj7", "drop3"),
    ("x 3 x 2 5 3", "1 6 3 5", "6", "drop3"),
    ("x 3 2 2 3 3", "1 3 6 9 5", "6/9", "drop3"),
    ("x 3 2 4 3 x", "1 3 7 9", "maj9", "drop3"),
    ("x 3 4 4 5 x", "1 #11 7 3", "maj7#11", "drop3"),
    ("x 3 x 3 5 3", "1 b7 3 5", "7", "drop3"),
    ("x 3 5 3 5 x", "1 5 b7 3", "7", "drop3"),
    ("x 3 2 3 3 3", "1 3 b7 9 5", "9", "drop3"),
    ("x 3 2 3 3 x", "1 3 b7 9", "9", "drop3"),
    ("x 3 x 3 5 5", "1 b7 3 13", "13", "drop3"),
    ("x 3 2 3 2 x", "1 3 b7 b9", "7b9", "drop3"),
    ("x 3 2 3 2 5", "1 3 b7 b9 13", "13b9", "drop3"),
    ("x 3 2 3 4 x", "1 3 b7 #9", "7#9", "drop3"),
    ("x 3 2 3 4 4", "1 3 b7 #9 #5", "7#5#9", "drop3"),
    ("x 3 2 3 2 4", "1 3 b7 b9 #5", "7#5b9", "drop3"),
    ("x 3 x 3 5 4", "1 b7 3 #5", "7#5", "drop3"),
    ("x 3 4 3 5 x", "1 b5 b7 3", "7b5", "drop3"),
    ("x 3 4 3 2 x", "1 b5 b7 b9", "7b5b9", "drop3"),
    ("x 3 x 3 6 3", "1 b7 4 5", "7sus4", "drop3"),
    ("x 3 3 3 3 3", "1 4 b7 9 5", "9sus4", "drop3"),
    ("x 3 x 3 4 3", "1 b7 b3 5", "m7", "drop3"),
    ("x 3 5 3 4 x", "1 5 b7 b3", "m7", "drop3"),
    ("x 3 x 2 4 3", "1 6 b3 5", "m6", "drop3"),
    ("x 3 1 2 1 x", "1 b3 6 1", "m6", "drop3"),
    ("x 3 1 3 3 x", "1 b3 b7 9", "m9", "drop3"),
    ("x 3 3 3 4 x", "1 11 b7 b3", "m11", "drop3"),
    ("x 3 x 4 4 3", "1 7 b3 5", "m(maj7)", "drop3"),
    ("x 3 5 4 4 x", "1 5 7 b3", "m(maj7)", "drop3"),
    ("x 3 x 3 4 2", "1 b7 b3 b5", "m7b5", "drop3"),
    ("x 3 4 3 4 x", "1 b5 b7 b3", "m7b5", "drop3 shell"),
    ("x 3 x 2 4 2", "1 bb7 b3 b5", "dim7", "drop3"),
    ("x 3 4 2 4 x", "1 b5 bb7 b3", "dim7", "drop3 shell"),
    # shells
    ("x 3 2 4 x x", "1 3 7", "maj7 shell", "shell"),
    ("x 3 2 2 x x", "1 3 6", "6 shell", "shell"),
    ("x 3 2 3 x x", "1 3 b7", "7 shell", "shell"),
    ("x 3 3 3 x x", "1 4 b7", "7sus4 shell", "shell"),
    ("x 3 1 3 x x", "1 b3 b7", "m7 shell", "shell"),
    ("x 3 1 2 x x", "1 b3 6", "m6 shell", "shell"),
    ("x 3 1 4 x x", "1 b3 7", "m(maj7) shell", "shell"),
    # triads
    ("x 3 5 5 5 x", "1 5 1 3", "major", "triad drop3"),
    ("x 3 5 5 4 x", "1 5 1 b3", "minor", "triad drop3"),
    ("x 3 x 5 5 4", "1 1 3 #5", "augmented", "triad drop3"),
    ("x 3 5 5 6 x", "1 5 1 4", "sus4", "triad drop3"),
]

GRIPS: list[Grip] = [_grip(*row) for row in _TABLE]

# how much each kind of chord tone matters when choosing between grips
_WEIGHT = {"third": 10, "seventh": 10, "sixth": 10, "ninth_2": 6}


def _weight(role: str, degree: str) -> int:
    if role in _WEIGHT:
        return _WEIGHT[role]
    if role == "fifth":
        return 6 if degree in ("b5", "#5") else 0
    if role == "ninth":
        return 6 if degree in ("b9", "#9") else 3
    if role == "eleventh":
        return 5 if degree == "#11" else 3
    if role == "thirteenth":
        return 5 if degree == "b13" else 4
    return 1


def best_grips(ch: Chord, style: str) -> list[Grip]:
    """Grips for this chord: nothing outside the symbol, the 3rd and 7th always, then as much colour as fits."""
    want = {}
    for role, d in ch.tones.items():
        want[DEGREE_SEMIS[d] % 12] = max(want.get(DEGREE_SEMIS[d] % 12, 0), _weight(role, d))
    has_seventh = "seventh" in ch.tones or "sixth" in ch.tones
    pool = [g for g in GRIPS if ("triad" in g.sets) == (not has_seventh and "ninth" not in ch.tones)
            and style in g.sets]
    scored = []
    for g in pool:
        semis = {r % 12 for r in g.rel if r is not None}
        if not semis <= set(want):
            continue  # would add a note the symbol doesn't ask for
        guide = [s for role, s in ((r, DEGREE_SEMIS[d] % 12) for r, d in ch.tones.items())
                 if role in ("third", "seventh", "sixth")]
        if not all(s in semis for s in guide):
            continue
        scored.append((sum(want[s] for s in semis), g))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    return [g for s, g in scored if s == top]


def place(g: Grip, ch: Chord, tuning: list[int], allow_open: bool, max_fret: int = 15):
    """Every position of grip g for chord ch on this tuning: (frets per string, degree labels, midi)."""
    out = []
    names = {DEGREE_SEMIS[d] % 12: d for d in ch.tones.values()}  # label with the chord's own spelling
    for r in range(0, max_fret + 1):
        if (tuning[g.root_string] + r) % 12 != ch.root.pc:
            continue
        root_midi = tuning[g.root_string] + r
        frets, midi, labels = [], [], []
        ok = True
        for s, rel in enumerate(g.rel):
            if rel is None:
                frets.append(None)
                labels.append("")
                continue
            f = root_midi + rel - tuning[s]
            if f < 0 or f > max_fret or (f == 0 and not allow_open):
                ok = False
                break
            frets.append(f)
            midi.append(root_midi + rel)
            labels.append(names.get(rel % 12, g.degrees[s]))
        if not ok:
            continue
        fr = [f for f in frets if f]
        if fr and max(fr) - min(fr) > MAX_SPAN:
            continue  # e.g. a 6th-string shape moved into drop-D tuning
        out.append((tuple(frets), tuple(labels), tuple(midi)))
    return out
