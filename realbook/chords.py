"""Real Book chord symbols -> spelled chord tones.

Handles the notation found in fake books: '-' for minor, 'maj'/'Δ' for major seventh,
'ø' and '-7b5' for half-diminished, '°'/'dim', '+'/'aug', 6, 69, 7, 9, 11, 13,
alterations (b5 #5 b9 #9 #11 b13, with or without parentheses), sus/sus4/sus2,
'alt', 'add9', slash bass, and parenthesised optional chords such as '(G-7)'.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pitch import Interval, Name, transpose_name

DEGREE_SEMIS = {
    "1": 0, "b2": 1, "2": 2, "b3": 3, "3": 4, "4": 5, "b5": 6, "5": 7, "#5": 8,
    "6": 9, "bb7": 9, "b7": 10, "7": 11, "b9": 1, "9": 2, "#9": 3, "11": 5, "#11": 6, "b13": 8, "13": 9,
}
# letter steps above the root for each degree (for correct spelling: the b3 of A is C, not B#)
DEGREE_STEPS = {
    "1": 0, "b2": 1, "2": 1, "b3": 2, "3": 2, "4": 3, "b5": 4, "5": 4, "#5": 4,
    "6": 5, "bb7": 6, "b7": 6, "7": 6, "b9": 1, "9": 1, "#9": 1, "11": 3, "#11": 3, "b13": 5, "13": 5,
}


@dataclass
class Chord:
    symbol: str  # as written, e.g. 'A-7b5'
    root: Name
    tones: dict[str, str]  # role -> degree label, e.g. {'root':'1','third':'b3','fifth':'b5','seventh':'b7'}
    bass: Name | None = None
    optional: bool = False  # written in parentheses
    quality: str = field(default="")  # normalized suffix, for display

    def degree_name(self, degree: str) -> Name:
        return transpose_name(self.root, Interval(DEGREE_STEPS[degree], DEGREE_SEMIS[degree]))

    def pcs(self) -> set[int]:
        s = {(self.root.pc + DEGREE_SEMIS[d]) % 12 for d in self.tones.values()}
        if self.bass:
            s.add(self.bass.pc)
        return s

    def family(self) -> str:
        t = self.tones
        if t.get("third") == "b3" and t.get("fifth") == "b5":
            return "dim" if t.get("seventh") in ("bb7", None) else "hdim"
        if t.get("third") == "b3":
            return "min"
        if t.get("seventh") == "b7":
            return "dom"
        if t.get("third") == "4":
            return "sus"
        return "maj"

    def transposed(self, iv: Interval, prefer_flats: bool) -> "Chord":
        from .pitch import simplify

        root = simplify(transpose_name(self.root, iv), prefer_flats)
        bass = simplify(transpose_name(self.bass, iv), prefer_flats) if self.bass else None
        sym = str(root) + self.quality + (f"/{bass}" if bass else "")
        if self.optional:
            sym = f"({sym})"
        return Chord(sym, root, dict(self.tones), bass, self.optional, self.quality)


class ChordParseError(ValueError):
    pass


_ALT_RE = re.compile(r"(b5|#5|b9|#9|#11|b13|add9|add11|add13|9|11|13|alt|sus4|sus2|sus)")


def parse_chord(symbol: str) -> Chord:
    raw = symbol.strip()
    optional = raw.startswith("(") and raw.endswith(")")
    s = raw.strip("()").strip()
    s = s.replace("♭", "b").replace("♯", "#").replace("Δ", "maj7").replace("ø", "-7b5")
    s = s.replace("°", "dim").replace("–", "-").replace("−", "-")
    m = re.match(r"([A-G](?:b|#)?)(.*)$", s)
    if not m:
        raise ChordParseError(f"no root in {symbol!r}")
    root = Name.parse(m.group(1))
    rest = m.group(2)
    bass = None
    if "/" in rest:
        rest, b = rest.rsplit("/", 1)
        if re.fullmatch(r"[A-G](b|#)?", b):
            bass = Name.parse(b)
        else:  # e.g. 6/9 written with a slash
            rest = rest + "/" + b
    rest = rest.replace("6/9", "69").replace("(", "").replace(")", "").replace(",", "").replace(" ", "")
    quality_display = rest

    tones = {"root": "1", "third": "3", "fifth": "5"}
    seventh = None
    # --- quality prefix ---
    minor = dim = aug = False
    major7 = False
    if rest.startswith(("-maj7", "mMaj7", "mmaj7", "minmaj7", "-M7")):
        minor, major7 = True, True
        rest = re.sub(r"^(-maj7|mMaj7|mmaj7|minmaj7|-M7)", "", rest)
        seventh = "7"
    elif rest.startswith(("-", "min", "mi")) or (rest.startswith("m") and not rest.startswith("maj")):
        minor = True
        rest = re.sub(r"^(-|min|mi|m)", "", rest)
    elif rest.startswith("dim"):
        dim = True
        rest = rest[3:]
    elif rest.startswith(("aug", "+")):
        aug = True
        rest = re.sub(r"^(aug|\+)", "", rest)
    if rest.startswith(("maj", "M", "ma")) and not rest.startswith("mi"):
        major7 = True
        rest = re.sub(r"^(maj|ma|M)", "", rest)
        if not re.match(r"(7|9|11|13)", rest):
            rest = "7" + rest  # 'Cmaj' alone = Cmaj7 in Real Book usage

    # --- main number ---
    num = None
    mnum = re.match(r"(69|13|11|9|7|6)", rest)
    if mnum:
        num = mnum.group(1)
        rest = rest[len(num):]

    if minor:
        tones["third"] = "b3"
    if dim:
        tones["third"], tones["fifth"] = "b3", "b5"
    if aug:
        tones["fifth"] = "#5"

    if num == "6":
        tones["sixth"] = "6"
    elif num == "69":
        tones["sixth"] = "6"
        tones["ninth"] = "9"
    elif num in ("7", "9", "11", "13"):
        if dim and not major7:
            seventh = "bb7"
        elif major7:
            seventh = "7"
        else:
            seventh = "b7"
        if num in ("9", "11", "13"):
            tones["ninth"] = "9"
        if num in ("11",):
            tones["eleventh"] = "11"
        if num == "13":
            tones["thirteenth"] = "13"
            if minor:
                tones["eleventh"] = "11"
    elif major7 and seventh is None:
        seventh = "7"
    if seventh:
        tones["seventh"] = seventh

    # --- alterations / additions ---
    pos = 0
    while pos < len(rest):
        ma = _ALT_RE.match(rest, pos)
        if not ma:
            raise ChordParseError(f"cannot read {rest[pos:]!r} in {symbol!r}")
        a = ma.group(1)
        pos = ma.end()
        if a in ("b5", "#5"):
            tones["fifth"] = a
        elif a in ("b9", "#9"):
            if a == "#9" and "ninth" in tones and tones["ninth"] == "b9":
                tones["ninth_2"] = a
            else:
                tones["ninth"] = a
        elif a in ("9", "add9"):
            tones["ninth"] = "9"
        elif a in ("11", "add11"):
            tones["eleventh"] = "11"
        elif a == "#11":
            tones["eleventh"] = "#11"
        elif a == "b13":
            tones["thirteenth"] = "b13"
        elif a in ("13", "add13"):
            tones["thirteenth"] = "13"
        elif a == "alt":
            tones.update({"ninth": "b9", "ninth_2": "#9", "thirteenth": "b13"})
            tones.pop("fifth", None)
            tones.setdefault("seventh", "b7")
        elif a in ("sus", "sus4"):
            tones["third"] = "4"
        elif a == "sus2":
            tones["third"] = "2"

    return Chord(raw, root, tones, bass, optional, quality_display)


def to_music21_figure(symbol: str) -> str:
    """Translate Real Book notation to music21's chord-symbol dialect (used only for cross-checking)."""
    s = symbol.strip("()")
    m = re.match(r"([A-G])(b|#)?(.*)$", s)
    root = m.group(1) + ("-" if m.group(2) == "b" else (m.group(2) or ""))
    q = m.group(3)
    q = re.sub(r"^-", "m", q)
    q = q.replace("/", "/").replace("(", "").replace(")", "")
    # bass note flats
    q = re.sub(r"/([A-G])b", r"/\1-", q)
    return root + q
