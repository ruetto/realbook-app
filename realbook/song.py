"""Song model (melody + chords per bar), loading, and key-correct transposition."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .chords import Chord, parse_chord
from .pitch import MAJOR_KEYS, Interval, Name, Pitch, interval_between, simplify, transpose_pitch


@dataclass
class Note:
    beats: float  # 1.0 = quarter note
    pitch: Pitch | None  # None = rest
    tie: bool = False  # tied into the next note
    uncertain: bool = False  # transcriber flagged this pitch as uncertain


@dataclass
class ChordEvent:
    beat: float  # position in the bar, 0-based
    chord: Chord


@dataclass
class Bar:
    chords: list[ChordEvent]
    notes: list[Note]
    double_bar_after: bool = False
    doubts: list[str] = field(default_factory=list)

    @property
    def beats(self) -> float:
        return sum(n.beats for n in self.notes)


@dataclass
class Song:
    title: str
    composer: str
    style: str
    key: Name
    time: tuple[int, int]
    bars: list[Bar]
    bars_per_line: int = 4
    source: str = ""
    line_breaks: list[int] | None = None  # bar indices after which a new line starts (from the scan)

    def breaks_after(self, bi: int) -> bool:
        if bi == len(self.bars) - 1:
            return False
        if self.line_breaks is not None:
            return bi in self.line_breaks
        return (bi + 1) % self.bars_per_line == 0

    def chord_events(self):
        for bi, b in enumerate(self.bars):
            for ev in b.chords:
                yield bi, ev

    # ------------------------------------------------------------ checks
    def check(self) -> list[str]:
        problems = []
        per_bar = self.time[0] * 4 / self.time[1]
        for i, b in enumerate(self.bars):
            if abs(b.beats - per_bar) > 1e-9 and i != len(self.bars) - 1:
                problems.append(f"bar {i + 1}: notes add up to {b.beats} beats, not {per_bar}")
            for ev in b.chords:
                if not 0 <= ev.beat < per_bar:
                    problems.append(f"bar {i + 1}: chord {ev.chord.symbol} at impossible beat {ev.beat}")
        return problems

    # ------------------------------------------------------------ transposition
    def transposed(self, key: str) -> "Song":
        target = Name.parse(key)
        if str(target) == str(self.key):
            return self
        prefer_flats = MAJOR_KEYS.get(key, -1) <= 0
        # choose up or down, whichever keeps the melody closer to where it was
        up = interval_between(self.key, target, +1)
        down = interval_between(self.key, target, -1)
        iv = up if abs(up.semis) <= abs(down.semis) else down
        return self._apply(iv, target, prefer_flats)

    def _apply(self, iv: Interval, target: Name, prefer_flats: bool) -> "Song":
        bars = []
        for b in self.bars:
            notes = []
            for n in b.notes:
                if n.pitch is None:
                    notes.append(n)
                    continue
                p = transpose_pitch(n.pitch, iv)
                if p.name.acc in ("bb", "##"):
                    nn = simplify(p.name, prefer_flats)
                    p = next(Pitch(nn, o) for o in range(p.octave - 1, p.octave + 2) if Pitch(nn, o).midi == p.midi)
                notes.append(replace(n, pitch=p))
            chords = [ChordEvent(ev.beat, ev.chord.transposed(iv, prefer_flats)) for ev in b.chords]
            bars.append(replace(b, notes=notes, chords=chords))
        return replace(self, key=target, bars=bars)


# ---------------------------------------------------------------- loading

def _note(tok: str) -> Note:
    unc = "?" in tok
    tie = tok.endswith("~") or tok.endswith("~?")
    t = tok.replace("?", "").rstrip("~")
    m = re.fullmatch(r"(\d+)(\.?)(r|[A-G](?:bb|b|##|#)?-?\d)", t)
    if not m:
        raise ValueError(f"bad note token {tok!r}")
    beats = 4.0 / int(m.group(1)) * (1.5 if m.group(2) else 1.0)
    pitch = None if m.group(3) == "r" else Pitch.parse(m.group(3))
    return Note(beats, pitch, tie, unc)


def load(path: str | Path) -> Song:
    return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def from_dict(d: dict) -> Song:
    bars = []
    for b in d["bars"]:
        chords = []
        for c in b["chords"]:
            if isinstance(c, str):  # bare list: 1 chord = beat 0, 2 chords = beats 0 and 2 (4/4)
                chords.append(c)
            else:
                chords.append(c)
        evs = []
        if all(isinstance(c, str) for c in chords):
            beats = {1: [0], 2: [0, 2], 3: [0, 2, 3], 4: [0, 1, 2, 3]}[len(chords)] if chords else []
            evs = [ChordEvent(bt, parse_chord(c)) for bt, c in zip(beats, chords)]
        else:
            evs = [ChordEvent(c["beat"], parse_chord(c["symbol"])) for c in chords]
        bars.append(Bar(evs, [_note(t) for t in b["notes"]], b.get("double_bar_after", False), list(b.get("doubts", []))))
    ts = tuple(int(x) for x in d.get("time", "4/4").split("/"))
    src = d.get("source", "")
    return Song(d["title"], d.get("composer", ""), d.get("style", ""), Name.parse(d["key"]), ts, bars,
                d.get("bars_per_line", 4), src if isinstance(src, str) else "", d.get("line_breaks"))
