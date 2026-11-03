"""Pitch spelling and transposition that keep letter names correct (Db stays Db, not C#)."""
from __future__ import annotations

import re
from dataclasses import dataclass

LETTERS = "CDEFGAB"
NATURAL_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ACC_SEMIS = {"bb": -2, "b": -1, "": 0, "#": 1, "##": 2}
SEMIS_ACC = {v: k for k, v in ACC_SEMIS.items()}


@dataclass(frozen=True)
class Name:
    """A spelled pitch class, e.g. Name('B', 'b')."""

    letter: str
    acc: str = ""

    @property
    def pc(self) -> int:
        return (NATURAL_PC[self.letter] + ACC_SEMIS[self.acc]) % 12

    def __str__(self) -> str:
        return self.letter + self.acc

    @staticmethod
    def parse(s: str) -> "Name":
        m = re.fullmatch(r"([A-G])(bb|b|##|#)?", s)
        if not m:
            raise ValueError(f"not a note name: {s!r}")
        return Name(m.group(1), m.group(2) or "")


@dataclass(frozen=True)
class Pitch:
    """A spelled pitch with octave (scientific: C4 = middle C = MIDI 60)."""

    name: Name
    octave: int

    @property
    def midi(self) -> int:
        return 12 * (self.octave + 1) + NATURAL_PC[self.name.letter] + ACC_SEMIS[self.name.acc]

    def __str__(self) -> str:
        return f"{self.name}{self.octave}"

    @staticmethod
    def parse(s: str) -> "Pitch":
        m = re.fullmatch(r"([A-G](?:bb|b|##|#)?)(-?\d)", s)
        if not m:
            raise ValueError(f"not a pitch: {s!r}")
        return Pitch(Name.parse(m.group(1)), int(m.group(2)))


@dataclass(frozen=True)
class Interval:
    """Transposition interval: letter steps (may be negative) and semitones."""

    steps: int
    semis: int


def transpose_name(n: Name, iv: Interval) -> Name:
    li = (LETTERS.index(n.letter) + iv.steps) % 7
    letter = LETTERS[li]
    want = (n.pc + iv.semis) % 12
    diff = (want - NATURAL_PC[letter]) % 12
    if diff > 6:
        diff -= 12
    if diff not in SEMIS_ACC:
        raise ValueError(f"cannot spell {n} + {iv}")
    return Name(letter, SEMIS_ACC[diff])


def transpose_pitch(p: Pitch, iv: Interval) -> Pitch:
    nn = transpose_name(p.name, iv)
    # octave follows the letter walk; fix by comparing MIDI
    target = p.midi + iv.semis
    for octv in range(p.octave - 3, p.octave + 4):
        q = Pitch(nn, octv)
        if q.midi == target:
            return q
    raise AssertionError("octave resolution failed")


def simplify(n: Name, prefer_flats: bool) -> Name:
    """Respell double accidentals and rarely-used names (Cb, Fb, E#, B#) to plain ones."""
    if n.acc in ("bb", "##") or str(n) in ("Cb", "Fb", "E#", "B#"):
        pc = n.pc
        for acc in (["", "b", "#"] if prefer_flats else ["", "#", "b"]):
            for letter in LETTERS:
                cand = Name(letter, acc)
                if cand.pc == pc and str(cand) not in ("Cb", "Fb", "E#", "B#"):
                    return cand
    return n


def interval_between(src: Name, dst: Name, direction: int) -> Interval:
    """Interval that takes key tonic src to dst, going up (direction=+1) or down (-1)."""
    steps = (LETTERS.index(dst.letter) - LETTERS.index(src.letter)) % 7
    semis = (dst.pc - src.pc) % 12
    if direction < 0 and (steps or semis):
        steps -= 7
        semis -= 12
    return Interval(steps, semis)


# Key signatures (major keys) -> which letters carry which accidental
SHARP_ORDER = "FCGDAEB"
FLAT_ORDER = "BEADGCF"
MAJOR_KEYS = {
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7,
}


def key_accidentals(key: str) -> dict[str, str]:
    n = MAJOR_KEYS[key]
    if n >= 0:
        return {letter: "#" for letter in SHARP_ORDER[:n]}
    return {letter: "b" for letter in FLAT_ORDER[: -n]}
