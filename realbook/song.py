"""Song model (melody + chords per bar), loading, and key-correct transposition."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .chords import Chord, parse_chord
from .pitch import MAJOR_KEYS, Interval, Name, Pitch, interval_between, simplify, transpose_name, transpose_pitch


@dataclass
class Note:
    beats: float  # 1.0 = quarter note (actual length, tuplets included)
    pitch: Pitch | None  # None = rest
    tie: bool = False  # tied into the next note
    uncertain: bool = False  # transcriber flagged this pitch as uncertain
    dur: str = ""  # written duration as in LilyPond ("8", "4.", "16"); empty = derive from beats
    tuplet: tuple[int, int] | None = None  # (3, 2) = triplet: 3 notes in the time of 2


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
    time: tuple[int, int] | None = None  # time signature change at the start of this bar
    key: Name | None = None  # key change at the start of this bar
    mode: str | None = None

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
    mode: str = "major"  # or "minor"
    pickup: float = 0.0  # beats in the pickup bar (bar 0); 0 = no pickup

    def breaks_after(self, bi: int) -> bool:
        if bi == len(self.bars) - 1:
            return False
        if self.line_breaks is not None:
            return bi in self.line_breaks
        n = bi + 1 - (1 if self.pickup else 0)  # a pickup bar doesn't count towards the line
        return n > 0 and n % self.bars_per_line == 0

    def relative_major(self) -> Name:
        if self.mode == "minor":
            return transpose_name(self.key, Interval(2, 3))
        return self.key

    def chord_events(self):
        for bi, b in enumerate(self.bars):
            for ev in b.chords:
                yield bi, ev

    # ------------------------------------------------------------ checks
    def bar_times(self) -> list[tuple[int, int]]:
        cur, out = self.time, []
        for b in self.bars:
            cur = b.time or cur
            out.append(cur)
        return out

    def check(self) -> list[str]:
        problems = []
        for i, (b, ts) in enumerate(zip(self.bars, self.bar_times())):
            per_bar = ts[0] * 4 / ts[1]
            if i == 0 and self.pickup:
                if abs(b.beats - self.pickup) > 1e-6:
                    problems.append(f"pickup bar: notes add up to {b.beats} beats, not {self.pickup}")
                continue
            if abs(b.beats - per_bar) > 1e-6 and i != len(self.bars) - 1:
                problems.append(f"bar {i + 1}: notes add up to {b.beats} beats, not {per_bar}")
            for ev in b.chords:
                if not 0 <= ev.beat < max(per_bar, self.pickup if i == 0 else 0):
                    problems.append(f"bar {i + 1}: chord {ev.chord.symbol} at impossible beat {ev.beat}")
        return problems

    # ------------------------------------------------------------ transposition
    def transposed(self, key: str) -> "Song":
        target = Name.parse(key)
        if str(target) == str(self.key):
            return self
        rel = transpose_name(target, Interval(2, 3)) if self.mode == "minor" else target
        prefer_flats = MAJOR_KEYS.get(str(simplify(rel, True)), -1) <= 0
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
            bkey = simplify(transpose_name(b.key, iv), prefer_flats) if b.key else None
            bars.append(replace(b, notes=notes, chords=chords, key=bkey))
        return replace(self, key=target, bars=bars)


# ---------------------------------------------------------------- loading

_NOTE_RE = re.compile(r"(\d+)(\.*)(r|[A-G](?:bb|b|##|#)?-?\d)(~?)(?:@(\d+)/(\d+))?")


def _note(tok: str) -> Note:
    """'4C4' quarter middle C, '2.Eb4' dotted half, '1F4~' tied, '4r' rest, '8D5@3/2' eighth in a triplet."""
    unc = "?" in tok
    t = tok.replace("?", "")
    m = _NOTE_RE.fullmatch(t)
    if not m:
        raise ValueError(f"bad note token {tok!r}")
    base = 4.0 / int(m.group(1))
    beats, add = base, base
    for _ in m.group(2):
        add /= 2
        beats += add
    tuplet = (int(m.group(5)), int(m.group(6))) if m.group(5) else None
    if tuplet:
        beats = beats * tuplet[1] / tuplet[0]
    pitch = None if m.group(3) == "r" else Pitch.parse(m.group(3))
    return Note(beats, pitch, m.group(4) == "~", unc, m.group(1) + m.group(2), tuplet)


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
        bt = tuple(int(x) for x in b["time"].split("/")) if b.get("time") else None
        bk = Name.parse(b["key"]) if b.get("key") else None
        bars.append(Bar(evs, [_note(t) for t in b["notes"]], b.get("double_bar_after", False),
                        list(b.get("doubts", [])), bt, bk, b.get("mode")))
    ts = tuple(int(x) for x in d.get("time", "4/4").split("/"))
    src = d.get("source", "")
    pickup = d.get("pickup") or 0
    if isinstance(pickup, str):
        from fractions import Fraction

        pickup = float(Fraction(pickup))
    return Song(d["title"], d.get("composer", ""), d.get("style", ""), Name.parse(d["key"]), ts, bars,
                d.get("bars_per_line", 4), src if isinstance(src, str) else "", d.get("line_breaks"),
                d.get("mode", "major"), float(pickup))
