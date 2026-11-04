"""Guitar voicing engine: shells, drop-2, drop-3 and rootless voicings for any tuning.

Voicings are not looked up in a table. For each chord the engine decides which chord
tones the style calls for, then searches the fretboard for every placement that sounds
exactly those tones in the style's vertical order and is physically playable. A
progression is then voiced by choosing, among all candidates, the sequence with the
smoothest voice leading and least hand movement (Viterbi search).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from .chords import DEGREE_SEMIS, Chord

STYLES = {
    "shell": "Shells (root, 3rd, 7th, plus the defining alteration)",
    "drop2": "Drop-2",
    "drop3": "Drop-3",
    "rootless": "Rootless (3rd, 7th, 9th, 5th or 13th)",
}

TUNINGS = {
    "standard": [40, 45, 50, 55, 59, 64],          # E A D G B E
    "half-step-down": [39, 44, 49, 54, 58, 63],    # Eb Ab Db Gb Bb Eb
    "drop-d": [38, 45, 50, 55, 59, 64],            # D A D G B E
    "all-fourths": [40, 45, 50, 55, 60, 65],       # E A D G C F
}

MAX_FRET = 15
FALLBACKS: list[tuple[str, str, str]] = []  # (chord, requested style, style used) for the last progression
MAX_SPAN = 4  # frets between lowest and highest fretted note (4 = a five-fret stretch)


@dataclass(frozen=True)
class Voicing:
    frets: tuple  # one entry per string, low string first; None = muted
    degrees: tuple  # degree label per string ('' if muted)
    midi: tuple  # sounding MIDI per played string, low to high

    @property
    def fretted(self):
        return [f for f in self.frets if f is not None and f > 0]

    @property
    def span(self):
        fr = self.fretted
        return (max(fr) - min(fr)) if fr else 0

    @property
    def avg_fret(self):
        fr = [f for f in self.frets if f is not None]
        return sum(fr) / len(fr)


# ---------------------------------------------------------------- tone selection

def _fifth_slot(t: dict, family: str, for_drop3: bool = False) -> str:
    """The 'fifth' voice, which in jazz voicings is usually swapped for a colour tone."""
    fifth = t.get("fifth", "5")
    if fifth in ("b5", "#5"):
        return fifth  # altered fifth defines the chord: always keep it
    if for_drop3:
        if t.get("ninth") in ("b9", "#9"):
            return t["ninth"]
        for k in ("thirteenth",):
            if k in t:
                return t[k]
        if t.get("eleventh") == "#11":
            return "#11"
        if "ninth" in t and "sixth" not in t:
            return t["ninth"]
        if t.get("eleventh") == "11":
            return "11"
        return fifth
    if "thirteenth" in t:
        return t["thirteenth"]
    if t.get("eleventh") == "#11":
        return "#11"
    if t.get("eleventh") == "11" and family == "min":
        return "11"
    return fifth


def style_tones(ch: Chord, style: str) -> tuple[list[str], str | None]:
    """Degrees to sound (low-to-high order not implied) and the degree required in the bass (or None)."""
    t = ch.tones
    fam = ch.family()
    third = t.get("third", "3")
    seventh = t.get("seventh") or t.get("sixth")
    bass_req = "1"

    if fam == "dim" and t.get("seventh") == "bb7":
        full = ["1", "b3", "b5", "bb7"]
        if style == "shell":
            return full, "1"
        return full, ("1" if style == "drop3" else None)

    if "ninth_2" in t:  # altered dominant: 3, b7, #9, b13 (or b9)
        alt = [third, "b7", t["ninth_2"], t.get("thirteenth", t.get("ninth", "b9"))]
        if style == "shell":
            return ["1", third, "b7", t["ninth_2"]], "1"
        if style == "drop3":
            return ["1", third, "b7", t["ninth_2"]], "1"
        return alt, None

    if seventh is None:  # plain triad (Bb, F-, C+, Esus4): play exactly the three written notes
        tones = ["1", third, t.get("fifth", "5")]
        if "ninth" in t:  # add9 chords keep the 9th
            tones.append(t["ninth"])
        return tones, ("1" if style in ("shell", "drop3") else None)

    if style == "shell":
        tones = ["1", third, seventh]
        extra = None
        if t.get("fifth") in ("b5", "#5"):
            extra = t["fifth"]
        elif t.get("ninth") in ("b9", "#9"):
            extra = t["ninth"]
        elif t.get("eleventh") == "#11":
            extra = "#11"
        elif t.get("thirteenth") == "b13":
            extra = "b13"
        elif "thirteenth" in t:
            extra = t["thirteenth"]
        elif "eleventh" in t:
            extra = t["eleventh"]
        elif "ninth" in t:
            extra = t["ninth"]
        if extra:
            tones.append(extra)
        return tones, "1"

    if style == "drop2":
        top = t.get("ninth", "1") if "sixth" not in t or "ninth" in t else "1"
        return [top, third, _fifth_slot(t, fam), seventh], None

    if style == "drop3":
        return ["1", third, _fifth_slot(t, fam, for_drop3=True), seventh], "1"

    if style == "rootless":
        if "ninth" in t:
            nine = t["ninth"]
        elif fam == "hdim":
            nine = "11"
        else:
            nine = "9"
        five = _fifth_slot(t, fam)
        if fam == "dom" and five == "5":
            five = "13"
        if five == nine or DEGREE_SEMIS[five] % 12 == DEGREE_SEMIS[nine] % 12:
            five = t.get("fifth", "5")
        return [third, seventh, nine, five], None

    raise ValueError(style)


# ---------------------------------------------------------------- fretboard search

def _string_sets(style: str, n_notes: int, n_strings: int) -> list[tuple[int, ...]]:
    sets = []
    if n_notes == 3 and style != "shell":
        # triads: three adjacent strings (root-in-bass styles also allow a skipped string above the bass)
        sets = [tuple(range(i, i + 3)) for i in range(0, n_strings - 2)]
        if style == "drop3":
            sets += [(i, i + 2, i + 3) for i in range(0, n_strings - 3)]
        return sets
    if style == "drop2":
        sets = [tuple(range(i, i + 4)) for i in range(0, n_strings - 3)]
    elif style == "drop3":
        sets = [(i, i + 2, i + 3, i + 4) for i in range(0, n_strings - 4)]
    elif style == "rootless":
        sets = [tuple(range(i, i + 4)) for i in range(1, n_strings - 3)]
    elif style == "shell":
        # bass on the 6th or 5th string, upper voices on adjacent strings from the 4th string up
        # (6-4-3, 5-4-3, and 6-4-3-2 / 5-4-3-2 with the added alteration): low thirds stay out of the mud
        for bass in (0, 1):
            s = (bass,) + tuple(range(2, 2 + n_notes - 1))
            if s[-1] < n_strings:
                sets.append(s)
    return sets


def _structure_ok(style: str, p: list[int]) -> bool:
    if any(b <= a for a, b in zip(p, p[1:])):
        return False  # voices must ascend from low string to high string
    if len(p) == 3 and style != "shell":
        return p[-1] - p[0] <= 16  # triad: compact, within about an octave and a third
    if style == "drop2":
        q = p[0] + 12
        return p[2] < q < p[3] and max(p[1:] + [q]) - min(p[1:] + [q]) < 12
    if style == "drop3":
        q = p[0] + 12
        return p[1] < q < p[2] and max(p[1:] + [q]) - min(p[1:] + [q]) < 12
    if style == "rootless":
        return p[-1] - p[0] <= 14
    if style == "shell":
        return p[-1] - p[0] <= 24
    return True


def candidates(ch: Chord, style: str, tuning: list[int], allow_open: bool = True) -> list[Voicing]:
    degrees, bass_req = style_tones(ch, style)
    n = len(degrees)
    root_pc = ch.root.pc
    want_pc = [(root_pc + DEGREE_SEMIS[d]) % 12 for d in degrees]
    out, seen = [], set()
    for sset in _string_sets(style, n, len(tuning)):
        for perm in set(itertools.permutations(range(n))):
            order = [degrees[i] for i in perm]
            pcs = [want_pc[i] for i in perm]
            if bass_req and order[0] != bass_req:
                continue
            options = []
            for s, pc in zip(sset, pcs):
                options.append([f for f in range(0, MAX_FRET + 1) if (tuning[s] + f) % 12 == pc])
            for frets in itertools.product(*options):
                if not allow_open and 0 in frets:
                    continue
                fretted = [f for f in frets if f > 0]
                if fretted and max(fretted) - min(fretted) > MAX_SPAN:
                    continue
                midi = [tuning[s] + f for s, f in zip(sset, frets)]
                if not _structure_ok(style, midi):
                    continue
                full = [None] * len(tuning)
                labels = [""] * len(tuning)
                for s, f, d in zip(sset, frets, order):
                    full[s] = f
                    labels[s] = d
                key = tuple(full)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Voicing(key, tuple(labels), tuple(midi)))
    return out


# ---------------------------------------------------------------- choosing a sequence

def _local_cost(v: Voicing, style: str) -> float:
    c = 0.0
    c += 0.2 * max(0.0, v.avg_fret - 8)          # stay in the lower-middle of the neck
    c += {4: 1.5, 3: 0.3}.get(v.span, 0.0)       # wide stretches are harder
    c += 0.6 * sum(1 for f in v.frets if f == 0)  # open strings ring on and sound un-jazzy
    if style == "drop2" and v.frets[0] is not None:
        c += 0.8                                  # drop-2 on the bottom four strings is muddy
    if v.midi[0] < 43 and style in ("drop2", "rootless"):
        c += 1.0
    return c


def _move_cost(a: Voicing, b: Voicing) -> float:
    if len(a.midi) == len(b.midi):
        vl = sum(abs(x - y) for x, y in zip(a.midi, b.midi))
    else:
        vl = sum(min(abs(x - y) for y in b.midi) for x in a.midi) + 3
    return 0.15 * vl + 0.4 * abs(a.avg_fret - b.avg_fret) + 0.2 * max(0, abs(a.midi[-1] - b.midi[-1]) - 5)


def voice_progression(chords: list[Chord], style: str, tuning: list[int], allow_open: bool = True) -> list[Voicing]:
    FALLBACKS.clear()
    if not chords:  # e.g. a page where no chord symbols were read: melody and tab only
        return []
    cands = []
    for c in chords:
        cs = candidates(c, style, tuning, allow_open) or candidates(c, style, tuning, True)
        if not cs:  # e.g. an unusual chord with no playable voicing in this style: borrow another style's
            for alt in ("drop2", "drop3", "shell", "rootless"):
                cs = candidates(c, alt, tuning, allow_open) or candidates(c, alt, tuning, True)
                if cs:
                    FALLBACKS.append((c.symbol, style, alt))
                    break
        if not cs:
            raise RuntimeError(f"no playable voicing for {c.symbol} in this tuning")
        cands.append(cs)
    # Viterbi
    cost = [_local_cost(v, style) + 0.1 * v.avg_fret for v in cands[0]]
    back = []
    for i in range(1, len(cands)):
        new_cost, ptr = [], []
        for v in cands[i]:
            lc = _local_cost(v, style)
            best_j, best = 0, float("inf")
            for j, u in enumerate(cands[i - 1]):
                c = cost[j] + _move_cost(u, v)
                if c < best:
                    best, best_j = c, j
            new_cost.append(best + lc)
            ptr.append(best_j)
        cost = new_cost
        back.append(ptr)
    j = min(range(len(cost)), key=cost.__getitem__)
    path = [j]
    for ptr in reversed(back):
        j = ptr[j]
        path.append(j)
    path.reverse()
    return [cands[i][k] for i, k in enumerate(path)]


# ---------------------------------------------------------------- verification

def verify(ch: Chord, style: str, v: Voicing, tuning: list[int]) -> list[str]:
    """Independent re-check of a voicing. Returns a list of problems (empty = correct)."""
    problems = []
    played = [(s, f, d) for s, (f, d) in enumerate(zip(v.frets, v.degrees)) if f is not None]
    midi = [tuning[s] + f for s, f, _ in played]
    if tuple(midi) != v.midi:
        problems.append("stored pitches do not match frets")
    # every sounding note must be the chord degree it is labelled as
    for (s, f, d), m in zip(played, midi):
        if (m - ch.root.pc) % 12 != DEGREE_SEMIS[d] % 12:
            problems.append(f"string {6 - s}: fret {f} is not the {d} of {ch.symbol}")
    want, bass_req = style_tones(ch, style)
    if sorted(d for _, _, d in played) != sorted(want):
        problems.append(f"voices {sorted(d for _, _, d in played)} != required {sorted(want)}")
    t = ch.tones
    sounding = {d for _, _, d in played}
    # guide tones and chord-defining alterations must be present
    third = t.get("third")
    if third and third not in sounding:
        problems.append(f"missing 3rd ({third})")
    sev = t.get("seventh") or t.get("sixth")
    if sev and sev not in sounding:
        problems.append(f"missing 7th/6th ({sev})")
    if t.get("fifth") in ("b5", "#5") and t["fifth"] not in sounding:
        problems.append(f"missing altered fifth {t['fifth']}")
    if t.get("ninth") in ("b9", "#9") and style != "shell" and not ({"b9", "#9"} & sounding):
        problems.append("missing altered ninth")
    # nothing outside the chord or its standard tensions
    allowed = {(ch.root.pc + DEGREE_SEMIS[d]) % 12 for d in t.values()}
    tensions = {"maj": ("9", "13"), "min": ("9", "11"), "dom": ("9", "13"), "sus": ("9", "13"), "hdim": ("11",), "dim": ()}
    allowed |= {(ch.root.pc + DEGREE_SEMIS[d]) % 12 for d in tensions[ch.family()]}
    for m in midi:
        if m % 12 not in allowed:
            problems.append(f"note {m} not in chord {ch.symbol}")
    if bass_req and v.degrees[[i for i, f in enumerate(v.frets) if f is not None][0]] != bass_req:
        problems.append("wrong bass note")
    if not _structure_ok(style, list(midi)):
        problems.append(f"not a valid {style} structure")
    fr = [f for _, f, _ in played if f > 0]
    if fr and max(fr) - min(fr) > MAX_SPAN:
        problems.append("stretch too wide")
    if any(f > MAX_FRET for _, f, _ in played):
        problems.append("too high on the neck")
    return problems
