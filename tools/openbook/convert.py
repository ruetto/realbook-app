"""OpenBook event dump -> realbook song JSON (melody, chords, key, time, pickup)."""
from __future__ import annotations

import json
import sys
from fractions import Fraction as F
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from realbook.chords import ChordParseError, parse_chord  # noqa: E402

LETTERS = "CDEFGAB"
NAT = [0, 2, 4, 5, 7, 9, 11]
ACC = {F(-1): "bb", F(-1, 2): "b", F(0): "", F(1, 2): "#", F(1): "##"}
FLATS, SHARPS = "BEADGCF", "FCGDAEB"
MAJ_BY_FLATS = ["C", "F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"]
MAJ_BY_SHARPS = ["C", "G", "D", "A", "E", "B", "F#", "C#"]
MIN_BY_FLATS = ["A", "D", "G", "C", "F", "Bb", "Eb", "Ab"]
MIN_BY_SHARPS = ["A", "E", "B", "F#", "C#", "G#", "D#", "A#"]


class Pitch:
    def __init__(self, nn, alt, octv):
        self.nn, self.alt, self.oct = int(nn), F(alt), int(octv)

    @property
    def semis(self):
        return NAT[self.nn] + int(self.alt * 2) + 12 * self.oct

    @property
    def steps(self):
        return self.nn + 7 * self.oct

    def name(self):
        return LETTERS[self.nn] + ACC[self.alt]

    def sci(self):
        return f"{self.name()}{self.oct + 4}"


def dur_beats(log, dots, scale):
    base = F(4, 2 ** int(log)) if int(log) >= 0 else F(4 * 2 ** (-int(log)))
    total, add = base, base
    for _ in range(int(dots)):
        add /= 2
        total += add
    return total * F(scale)


DUR_TOKEN = {0: "1", 1: "2", 2: "4", 3: "8", 4: "16", 5: "32", -1: "\\breve"}


def parse_events(path: Path):
    mel, chords, keys, times = [], {}, [], []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        head, *rest = line.split(" | ")
        h = head.split()
        tag, kind = h[0], h[1]
        if kind == "K":
            keys.append((F(h[2]), Pitch(h[3], h[4], h[5]), h[6]))
            continue
        if kind == "T":
            times.append((F(h[2]), h[3]))
            continue
        now, grace, bar, pos = F(h[2]), F(h[3]), int(h[4]), F(h[5])
        if grace != 0:
            continue  # grace notes are ornaments; not written into the lead sheet
        d = rest[0].split()
        p = rest[1].split()
        flags = rest[2].split()
        rec = {"now": now, "bar": bar, "pos": pos, "dur": d, "pitch": None if p[0] == "-" else Pitch(*p),
               "bass": flags[0] == "bass", "inv": flags[1] == "inv", "tie": flags[2] == "tie", "kind": kind}
        if tag == "M":
            mel.append(rec)
        else:
            chords.setdefault(now, []).append(rec)
    return mel, chords, keys, times


def key_name(tonic: Pitch, alist: str) -> tuple[str, str]:
    accs = [F(x.split(":")[1]) for x in alist.split(",") if x]
    flats, sharps = sum(1 for a in accs if a < 0), sum(1 for a in accs if a > 0)
    maj = MAJ_BY_FLATS[flats] if flats else MAJ_BY_SHARPS[sharps]
    mnr = MIN_BY_FLATS[flats] if flats else MIN_BY_SHARPS[sharps]
    t = tonic.name()
    if t == mnr:
        return mnr, "minor"
    return maj, "major"


# ------------------------------------------------------------------ chord naming

def chord_symbol(notes: list[dict], root=None) -> tuple[str, set]:
    pitches = [n["pitch"] for n in notes]
    added_bass = next((n["pitch"] for n in notes if n["bass"]), None)  # c/+e: extra note below
    inv = next((n["pitch"] for n in notes if n["inv"]), None)  # c/e: a chord tone moved to the bass
    body = [n["pitch"] for n in notes if not n["bass"]]
    if root is None:
        root = min((p for p in body if p is not inv), key=lambda p: (p.steps, p.semis))
    degs = set()
    for p in body:
        if p is root:
            continue
        st = p.steps - root.steps
        semis = p.semis - root.semis
        if p is inv or st < 0:  # tones below the root (inversions): measure upwards within one octave
            while st < 0:
                st += 7
                semis += 12
        octs = st // 7
        natural = NAT[st % 7] + 12 * octs
        alt = semis - natural
        deg = st % 7 + 1
        if deg in (2, 4, 6) and st >= 7:
            deg += 7
        degs.add((deg, alt))
    has = lambda d, a=0: (d, a) in degs  # noqa: E731
    minor, major = has(3, -1), has(3)
    five = "5" if has(5) else "b5" if has(5, -1) else "#5" if has(5, 1) else None
    sev = "7" if has(7) else "b7" if has(7, -1) else "bb7" if has(7, -2) else None
    six = has(6)
    n9 = has(9) or has(2)
    b9, s9 = has(9, -1) or has(2, -1), has(9, 1) or has(2, 1)
    n11 = has(11) or (has(4) and (minor or major))
    s11 = has(11, 1) or (has(4, 1) and (minor or major))
    n13, b13 = has(13), has(13, -1) or (has(6, -1) and sev is not None)
    sus4 = not minor and not major and (has(4) or has(11))
    sus2 = not minor and not major and not sus4 and has(2)
    r = root.name()
    bass = added_bass or inv
    slash = f"/{bass.name()}" if bass is not None and bass.name() != r else ""
    # OpenBook's "7alt" = 1 b3 b5 b7 b9 b11 b13, i.e. a dominant with #9, 3 (as b11), b5, b9, b13
    if minor and sev == "b7" and (has(11, -1) or has(4, -1)):
        return r + "7alt" + slash, {p.semis % 12 for p in pitches}
    alts = []
    if minor and five == "b5" and sev == "bb7":
        sym = r + "dim7"
    elif minor and five == "b5" and sev is None and not six:
        sym = r + "dim"
    elif major and five == "#5" and sev in (None, "b7"):
        sym = r + ("+" if sev is None else "+7")
        for flag, txt in ((n9, "9"), (b9, "b9"), (s9, "#9"), (s11, "#11")):
            if flag:
                sym += txt
    else:
        q = "-" if minor else ""
        if sev == "7":
            num = "13" if (n13 and n9) else "9" if n9 else "7"
            q += "maj" + num
            if n13 and not n9:
                alts.append("13")
        elif sev == "b7":
            num = "13" if (n13 and n9 and not minor) else "11" if (minor and n11 and n9) else "9" if n9 else "7"
            q += num
            if n13 and num != "13":
                alts.append("13")
            if n11 and num not in ("11", "13") and not sus4:
                alts.append("11")
        elif sev == "bb7":
            q += "dim7" if not minor else ""
        elif six:
            q += "69" if n9 else "6"
        elif n9 and not sus2:
            q += "add9"
        if five == "b5":
            alts.append("b5")
        if five == "#5":
            alts.append("#5")
        for flag, txt in ((b9, "b9"), (s9, "#9"), (s11, "#11"), (b13, "b13")):
            if flag:
                alts.append(txt)
        if n11 and sev is None and not sus4:
            alts.append("add11")
        if sus4:
            q += "sus4"
        if sus2:
            q += "sus2"
        sym = r + q + "".join(alts)
    return sym + slash, {p.semis % 12 for p in pitches}


def _check(sym, want):
    try:
        got = parse_chord(sym).pcs()
    except ChordParseError:
        return False
    if got == want:
        return True
    return "7alt" in sym and got <= want  # 'alt' is a family of altered dominants; voicings use a subset


def name_chord(notes):
    sym, want = chord_symbol(notes)
    if _check(sym, want) and not any(n["inv"] for n in notes):
        return sym, True, want
    # inversions (and anything that didn't check out): try every chord tone as the root and keep the
    # simplest name that parses back to exactly these notes
    best = None
    for n in notes:
        if n["bass"]:
            continue
        cand, _ = chord_symbol(notes, root=n["pitch"])
        if not _check(cand, want):
            continue
        q = cand.split("/")[0][len(n["pitch"].name()):]
        score = (len(q) + 3 * q.count("sus") + 2 * sum(q.count(x) for x in ("b5", "#5", "b9", "#9", "#11", "b13", "add")),
                 0 if n["inv"] is False else 1)
        if best is None or score < best[0]:
            best = (score, cand)
    if best:
        return best[1], True, want
    return sym, False, want


# ------------------------------------------------------------------ song

def convert(events: Path, md: dict) -> tuple[dict, list[str]]:
    mel, chords, keys, times = parse_events(events)
    problems = []
    attrs = md.get("attributes", {})
    ts = times[0][1] if times else "4/4"
    num, den = (int(x) for x in ts.split("/"))
    per_bar = F(num * 4, den)
    # key
    key, mode = ("C", "major")
    if keys:
        key, mode = key_name(keys[0][1], keys[0][2])
    # melody: group by moment, top note of any double stop
    by_now = {}
    for e in mel:
        by_now.setdefault(e["now"], []).append(e)
    bars: dict[int, list] = {}
    pickup = F(0)
    busy_until = None
    for now in sorted(by_now):
        evs = by_now[now]
        if busy_until is not None and now < busy_until:
            continue  # a second voice inside a note we kept: the lead sheet shows the top line only
        e = max(evs, key=lambda x: x["pitch"].semis if x["pitch"] else -999)
        busy_until = now + dur_beats(*e["dur"][:2], F(e["dur"][2])) / 4
        log, dots, scale = e["dur"]
        beats = dur_beats(log, dots, F(scale))
        bar = e["bar"]
        if e["pos"] < 0:
            bar = 0  # pickup
            pickup += beats
        if e["kind"] == "MR":
            continue  # empty bars are filled with rests of the right length below
        tok = _token(log, dots, F(scale), e["pitch"], e["tie"], problems)
        bars.setdefault(bar, []).append({"tok": tok, "beats": beats, "now": now})
    # chords
    cbars: dict[int, list] = {}
    bad = 0
    for now in sorted(chords):
        notes = [n for n in chords[now] if n["kind"] == "N"]
        if not notes:
            continue
        e = notes[0]
        bar = e["bar"] if e["pos"] >= 0 else 0
        # measurePosition is in whole notes; in a pickup bar it runs from -pickup to 0
        beat = e["pos"] * 4 if e["pos"] >= 0 else e["pos"] * 4 + pickup
        sym, ok, want = name_chord(notes)
        if not ok:
            bad += 1
            problems.append(f"bar {bar}: chord {sym} does not match its notes {sorted(want)}")
        cbars.setdefault(bar, []).append({"beat": float(beat) if beat.denominator != 1 else int(beat), "symbol": sym})
    # time and key in effect at the start of each bar (from the melody's own events)
    bar_start = {}
    for e in mel:
        b = 0 if e["pos"] < 0 else e["bar"]
        bar_start.setdefault(b, e["now"] - (0 if e["pos"] < 0 else e["pos"]))
    times_sorted = sorted(times)
    keys_sorted = sorted(keys, key=lambda k: k[0])

    def at(lst, t, default):
        cur = default
        for m, v, *rest in lst:
            if m <= t:
                cur = (v, *rest) if rest else v
        return cur

    first = 0 if 0 in bars else 1
    last = max(list(bars) + list(cbars))
    out_bars = []
    prev_ts, prev_key = ts, (key, mode)
    for b in range(first, last + 1):
        notes = bars.get(b, [])
        toks = [n["tok"] for n in notes if n["tok"]]
        entry = {"chords": cbars.get(b, []), "notes": toks}
        t0 = bar_start.get(b)
        if t0 is not None:
            bts = at(times_sorted, t0, ts)
            if bts != prev_ts and b != first:
                entry["time"] = bts
            prev_ts = bts
            k = at(keys_sorted, t0, None)
            if k is not None:
                kk = key_name(k[0], k[1])
                if kk != prev_key and b != first:
                    entry["key"], entry["mode"] = kk
                prev_key = kk
        if not toks and b != 0:
            n, d = (int(x) for x in prev_ts.split("/"))
            entry["notes"] = rests_for(F(n * 4, d))
        out_bars.append(entry)
    song = {
        "title": attrs.get("title", "Untitled"), "composer": attrs.get("composer", ""),
        "style": attrs.get("piece", ""), "key": key, "mode": mode, "time": ts,
        "pickup": str(pickup) if pickup else None, "bars_per_line": 4, "bars": out_bars,
        "source": "OpenBook (github.com/veltzer/openbook), " + md.get("default_version", "Real") + " version",
    }
    return song, problems


def rests_for(beats: F) -> list[str]:
    out = []
    for b, t in ((F(4), "1r"), (F(3), "2.r"), (F(2), "2r"), (F(1), "4r"), (F(1, 2), "8r")):
        while beats >= b:
            out.append(t)
            beats -= b
    return out


def _token(log, dots, scale, pitch, tie, problems):
    log = int(log)
    d = DUR_TOKEN.get(log, "4") + "." * int(dots)
    p = "r" if pitch is None else pitch.sci()
    t = d + p + ("~" if tie else "")
    if scale != 1:
        # tuplet: scale = den/num (e.g. 2/3 for triplets: 3 in the time of 2)
        t += f"@{scale.denominator}/{scale.numerator}"
    return t


