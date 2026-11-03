"""Turn recognizer output (one **kern + **mxhm text per staff) into a song file with review flags.

Rules applied on top of the raw output, all standard notation practice:
- the key signature of the first system applies to every system (Real Book pages print it once);
- an accidental lasts until the end of its bar;
- chords are written the Real Book way (A-7b5, not A:hdim7);
- each bar gets "doubt" flags when something looks wrong, so the review screen can point at it.
"""
from __future__ import annotations

import re

from ..chords import ChordParseError, parse_chord

FLATS = "BEADGCF"
SHARPS = "FCGDAEB"
MAJOR_BY_FLATS = ["C", "F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"]
MAJOR_BY_SHARPS = ["C", "G", "D", "A", "E", "B", "F#", "C#"]

QUALITY = {
    "maj7": "maj7", "min7": "-7", "7": "7", "9": "9", "11": "11", "13": "13", "maj6": "6", "6": "6",
    "min6": "-6", "hdim7": "-7b5", "dim7": "°7", "dim": "°", "maj": "", "min": "-", "min9": "-9",
    "min11": "-11", "maj9": "maj9", "minmaj7": "-maj7", "sus4": "sus4", "aug": "+", "aug7": "+7", "none": "",
}
SUSPICIOUS_EXTENSIONS = {"9", "11", "13", "-9", "-11", "maj9"}


def harte_to_realbook(c: str) -> str | None:
    if ":" not in c:
        return None
    root, q = c.split(":", 1)
    root = root.replace("-", "b")
    bass = None
    if "/" in q:
        q, bass = q.split("/", 1)
        bass = bass.replace("-", "b")
    base = q.split("(")[0]
    ext = q[q.index("(") + 1: q.rindex(")")] if "(" in q else ""
    if base not in QUALITY:
        return None
    exts = "".join(e for e in ext.split(",") if e and e != "no5")
    sym = root + QUALITY[base] + exts
    if bass and re.fullmatch(r"[A-G][b#]?", bass):
        sym += "/" + bass
    return sym


KERN_NOTE = re.compile(r"^([\[\(]*)(\d+)(\.*)([a-gA-G]+|r)([-#n]*)([^\s]*)$")


def _kern_note(tok: str):
    tok = tok.replace("L", "").replace("J", "").replace("(", "").replace(")", "")
    m = KERN_NOTE.match(tok)
    if not m:
        return None
    pre, num, dots, letters, acc, post = m.groups()
    if int(num) == 0:
        return None
    beats = 4.0 / int(num)
    add = beats
    for _ in dots:
        add /= 2
        beats += add
    tie_start = "[" in pre or "_" in post
    if letters == "r":
        return beats, None, None, None, False
    L = letters[0].upper()
    octave = 4 + (len(letters) - 1) if letters[0].islower() else 3 - (len(letters) - 1)
    a = {"-": "b", "--": "bb", "#": "#", "##": "##", "n": ""}.get(acc, None) if acc else None
    return beats, L, octave, a, tie_start


def key_from_signature(line: str) -> str | None:
    m = re.search(r"\*k\[([^\]]*)\]", line)
    if not m:
        return None
    sig = m.group(1)
    flats, sharps = sig.count("-"), sig.count("#")
    return MAJOR_BY_FLATS[flats] if flats else MAJOR_BY_SHARPS[sharps]


DUR_TOKEN = {4.0: "1", 3.0: "2.", 2.0: "2", 1.5: "4.", 1.0: "4", 0.75: "8.", 0.5: "8", 0.25: "16", 6.0: "1.", 3.5: "2.."}


def staves_to_song(kerns: list[str], title: str = "Untitled", sources: list[dict] | None = None) -> dict:
    """kerns: one recognizer output per staff, in reading order (may span several pages)."""
    # The page key: the recognizer reads each system on its own and sometimes misses or invents the
    # signature, so take a vote over all systems (Real Book songs don't change key mid-page).
    votes: dict[str, int] = {}
    for k in kerns:
        for line in k.splitlines():
            ks = key_from_signature(line)
            if ks:
                votes[ks] = votes.get(ks, 0) + 1
                break
    signed = {k: v for k, v in votes.items() if k != "C"}
    page_key = max(signed, key=signed.get) if signed else "C"
    flats = set(FLATS[: MAJOR_BY_FLATS.index(page_key)]) if page_key in MAJOR_BY_FLATS else set()
    sharps = set(SHARPS[: MAJOR_BY_SHARPS.index(page_key)]) if page_key in MAJOR_BY_SHARPS else set()
    time_sig = "4/4"
    for line in kerns[0].splitlines() if kerns else []:
        m = re.match(r"\*M(\d+/\d+)", line)
        if m:
            time_sig = m.group(1)
            break
    num, den = (int(x) for x in time_sig.split("/"))
    per_bar = num * 4 / den

    bars, line_breaks, systems = [], [], []
    for si, kern in enumerate(kerns):
        first_bar = len(bars)
        cur = None
        for raw in kern.splitlines():
            cols = raw.split("\t")
            k = cols[0].strip()
            h = cols[1].strip() if len(cols) > 1 else "."
            if not k:
                continue
            if k.startswith("="):
                if cur is not None and (cur["notes"] or cur["chords"]):
                    bars.append(cur)
                cur = None
                continue
            if k.startswith(("*", "!")):
                continue
            if cur is None:
                cur = {"notes": [], "chords": [], "_beat": 0.0, "_acc": {}, "doubts": [], "_last_start": 0.0, "_last_dur": 0.0}
            if h not in (".", "") and not h.startswith("*"):
                # a chord on a sustain row ('.') falls inside the last note: place it halfway through
                beat = cur["_beat"] if k != "." else cur["_last_start"] + cur["_last_dur"] / 2
                sym = harte_to_realbook(h)
                if sym is None:
                    cur["doubts"].append(f"unreadable chord '{h}'")
                else:
                    cur["chords"].append({"beat": int(min(per_bar - 1, round(beat))), "symbol": sym, "_raw": h})
            if k == ".":
                continue
            n = _kern_note(k)
            if n is None:
                cur["doubts"].append(f"unreadable note '{k}'")
                continue
            beats, L, octave, acc, tie = n
            if beats not in DUR_TOKEN:  # a duration the song format can't hold: use the nearest and flag it
                near = min(DUR_TOKEN, key=lambda d: abs(d - beats))
                cur["doubts"].append(f"unusual duration ({beats:g} beats) read as {near:g}")
                beats = near
            if L is None:
                tokn = f"{DUR_TOKEN[beats]}r"
            else:
                if acc is not None:
                    cur["_acc"][(L, octave)] = acc
                    a = acc
                elif (L, octave) in cur["_acc"]:
                    a = cur["_acc"][(L, octave)]
                else:
                    a = "b" if L in flats else ("#" if L in sharps else "")
                tokn = f"{DUR_TOKEN[beats]}{L}{a}{octave}" + ("~" if tie else "")
            cur["notes"].append(tokn)
            cur["_last_start"], cur["_last_dur"] = cur["_beat"], beats
            cur["_beat"] += beats
        if cur is not None and (cur["notes"] or cur["chords"]):
            bars.append(cur)
        if len(bars) > first_bar:
            line_breaks.append(len(bars) - 1)
            systems.append({"first_bar": first_bar, "last_bar": len(bars) - 1, **((sources or [{}])[si] if sources else {})})

    # per-bar checks
    for i, b in enumerate(bars):
        total = b.pop("_beat")
        for tmp in ("_acc", "_last_start", "_last_dur"):
            b.pop(tmp)
        if abs(total - per_bar) > 1e-6 and i != len(bars) - 1:
            b["doubts"].append(f"notes add up to {total:g} beats, not {per_bar:g}")
        seen = set()
        clean = []
        for c in b["chords"]:
            if c["beat"] in seen:
                b["doubts"].append("two chords on one beat")
                continue
            seen.add(c["beat"])
            try:
                parse_chord(c["symbol"])
            except ChordParseError:
                b["doubts"].append(f"chord {c['symbol']} not understood")
                continue
            q = re.sub(r"^[A-G][b#]?", "", c["symbol"])
            if q in SUSPICIOUS_EXTENSIONS:
                b["doubts"].append(f"check {c['symbol']}: 9/11/13 extensions are often misread")
            clean.append({"beat": c["beat"], "symbol": c["symbol"]})
        b["chords"] = sorted(clean, key=lambda c: c["beat"])
        if not b["notes"]:
            b["doubts"].append("no melody read")
    # repeated sections: identical melody bars should usually carry the same chords
    sig = [tuple(b["notes"]) for b in bars]
    for i in range(len(bars)):
        for j in range(i):
            if sig[i] == sig[j] and len(sig[i]) > 1 and bars[i]["chords"] and bars[j]["chords"]:
                ci = [c["symbol"] for c in bars[i]["chords"]]
                cj = [c["symbol"] for c in bars[j]["chords"]]
                if ci != cj:
                    bars[i]["doubts"].append(f"same melody as bar {j + 1} but chords differ ({' '.join(cj)})")
                break
    return {
        "title": title, "composer": "", "style": "", "key": page_key, "time": time_sig,
        "bars": bars, "line_breaks": line_breaks, "systems": systems,
        "review": {"status": "draft"}, "source": {"kern": kerns},
    }
