"""Melody fingering that follows standard guitar practice.

1. Play in position. The hand sits at a position p (index finger on fret p) and covers
   frets p..p+3, with one-fret stretches to p-1 or p+4 allowed at a small cost.
2. Choose the position for the phrase, preferring the lowest one that holds it.
3. Shift only where it is natural: after a rest, after a long note, or at a phrase
   boundary (the start of a Real Book line). A shift anywhere else is heavily penalised.
4. Avoid open strings (default) unless a note has no fretted placement at all.
5. Finger repeated phrases exactly like their first occurrence.

The search is a Viterbi pass over states (position, string, fret), so the result is the
best fingering under these rules, not a note-by-note guess.
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_TAB_FRET = 17
MAX_POSITION = 14


@dataclass
class MelodyEvent:
    bar: int
    midi: int | None  # None = rest
    beats: float
    tie: bool  # tied into the next event


@dataclass(frozen=True)
class Placement:
    string: int  # 0 = lowest string
    fret: int
    position: int  # index-finger fret of the hand position this note is played in

    def as_tuple(self):
        return (self.string, self.fret)


# ---------------------------------------------------------------- costs

POSITION_WEIGHT = 0.03   # per note, per fret of position: among comfortable positions, prefer lower
STRETCH_COST = 3.0       # note at p-1 or p+4: comfort first, so stretches cost more than a higher position
STRING_CROSS = 0.05      # per string crossed
OPEN_ALLOWED_COST = 0.1
OPEN_FORCED_COST = 4.0   # an open string when the player asked to avoid them
SHIFT_AT_BREAK = 2.5     # shift where the music breathes
SHIFT_MID_PHRASE = 12.0  # shift in the middle of a phrase
SHIFT_PER_FRET = 0.3


def _states(midi: int, tuning: list[int], open_strings: str, fixed_position: int | None):
    out = []
    has_fretted = any(1 <= midi - o <= MAX_TAB_FRET for o in tuning)
    for s, o in enumerate(tuning):
        f = midi - o
        if not 0 <= f <= MAX_TAB_FRET:
            continue
        if f == 0:
            if open_strings == "avoid" and has_fretted:
                continue
            cost = OPEN_ALLOWED_COST if open_strings == "allow" else OPEN_FORCED_COST
            positions = range(1, MAX_POSITION + 1) if fixed_position is None else [fixed_position]
            for p in positions:
                out.append((Placement(s, 0, p), cost))
            continue
        positions = range(max(1, f - 4), min(MAX_POSITION, f + 1) + 1)
        for p in positions:
            if fixed_position is not None and p != fixed_position:
                continue
            if p <= f <= p + 3:
                c = 0.0
            elif f == p - 1 or f == p + 4:
                c = STRETCH_COST
            else:
                continue
            out.append((Placement(s, f, p), c))
    return out


def _shift_point(events: list[MelodyEvent], i: int, bars_per_line: int) -> bool:
    """True if a position shift into note i would be natural."""
    j = i - 1
    saw_rest = False
    while j >= 0 and events[j].midi is None:
        saw_rest = True
        j -= 1
    if saw_rest or j < 0:
        return True
    if events[j].beats >= 2 and not events[j].tie:
        return True
    if events[i].bar != events[j].bar and events[i].bar % bars_per_line == 0:
        return True
    return False


# ---------------------------------------------------------------- repeated phrases

def _repeat_map(events: list[MelodyEvent], min_bars: int = 2) -> dict[int, int]:
    """Map event index -> index of the same note in an earlier identical run of >= min_bars bars."""
    bars: dict[int, list[int]] = {}
    for i, e in enumerate(events):
        bars.setdefault(e.bar, []).append(i)
    order = sorted(bars)
    sig = {b: tuple((events[i].midi, events[i].beats, events[i].tie) for i in bars[b]) for b in order}
    mapping: dict[int, int] = {}
    k = 0
    while k < len(order):
        best_len, best_src = 0, None
        for src in range(k):
            n = 0
            while k + n < len(order) and src + n < k and sig[order[src + n]] == sig[order[k + n]]:
                n += 1
            if n > best_len:
                best_len, best_src = n, src
        if best_len >= min_bars and any(events[i].midi is not None for b in order[k:k + best_len] for i in bars[b]):
            for n in range(best_len):
                for a, b in zip(bars[order[best_src + n]], bars[order[k + n]]):
                    mapping[b] = mapping.get(a, a)
            k += best_len
        else:
            k += 1
    return mapping


# ---------------------------------------------------------------- search

def _viterbi(events, tuning, bars_per_line, open_strings, fixed_position, forced: dict[int, Placement]):
    idx = [i for i, e in enumerate(events) if e.midi is not None]
    cand = []
    for i in idx:
        sts = _states(events[i].midi, tuning, open_strings, fixed_position)
        if i in forced:
            sts = [(pl, c) for pl, c in sts if pl == forced[i]] or [(forced[i], 0.0)]
        if not sts:
            raise ValueError(f"note {events[i].midi} cannot be played in this tuning/position")
        cand.append(sts)
    cost = [c + POSITION_WEIGHT * pl.position for pl, c in cand[0]]
    back = []
    for k in range(1, len(idx)):
        i, ip = idx[k], idx[k - 1]
        brk = _shift_point(events, i, bars_per_line)
        tied = events[ip].tie and i == ip + 1 and events[ip].midi == events[i].midi
        ncost, ptr = [], []
        for pl, c in cand[k]:
            best, bj = float("inf"), 0
            for j, (u, _) in enumerate(cand[k - 1]):
                if tied and (u.string, u.fret) != (pl.string, pl.fret):
                    continue
                t = cost[j] + STRING_CROSS * abs(u.string - pl.string)
                if u.position != pl.position:
                    t += (SHIFT_AT_BREAK if brk else SHIFT_MID_PHRASE) + SHIFT_PER_FRET * abs(u.position - pl.position)
                if t < best:
                    best, bj = t, j
            ncost.append(best + c + POSITION_WEIGHT * pl.position)
            ptr.append(bj)
        cost = ncost
        back.append(ptr)
    j = min(range(len(cost)), key=cost.__getitem__)
    path = [j]
    for ptr in reversed(back):
        j = ptr[j]
        path.append(j)
    path.reverse()
    out: list[Placement | None] = [None] * len(events)
    for i, k, c in zip(idx, path, cand):
        out[i] = c[k][0]
    return out


def finger_melody(events: list[MelodyEvent], tuning: list[int], bars_per_line: int = 4,
                  open_strings: str = "avoid", position: str | int = "auto") -> list[Placement | None]:
    fixed = None if position == "auto" else int(position)
    result = _viterbi(events, tuning, bars_per_line, open_strings, fixed, {})
    rep = _repeat_map(events)
    # constrain repeats to copy their first occurrence; repeat until the first occurrences stop changing
    for _ in range(5):
        if all(result[a] == result[b] for b, a in rep.items()):
            break
        forced = {b: result[a] for b, a in rep.items() if result[a] is not None}
        result = _viterbi(events, tuning, bars_per_line, open_strings, fixed, forced)
    return result
