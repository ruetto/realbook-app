"""A printable songbook: cover, index of tunes, every lead sheet, chord glossary and composer index.

Songs are engraved in groups (one LilyPond run per group, several runs in parallel), so the music fonts are
stored once per group instead of once per song. Each group is cached: building the book again, or after
adding or removing a few songs, only engraves the groups that changed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import pymupdf

from .engraver import lilypond_exe
from .render_ly import (FRET_FUNCTION, LY_VERSION, NOTE_NAMES, STYLES, Arrangement, arrange, chord_name_markup,
                        compile_pdf, diagram_markup, paper_block, song_parts)
from .song import from_dict

BOOK_VERSION = "book-1"  # bump when the engraved output of the songbook changes
N_GROUPS = 64  # songs are spread over this many cached groups by a stable hash of their id
A4 = (595.28, 841.89)
INK = (0.12, 0.15, 0.24)  # deep ink blue
ACCENT = (0.71, 0.51, 0.16)  # brass
GREY = (0.45, 0.47, 0.52)


@dataclass
class BookOptions:
    title: str = "Jazz Standards"
    subtitle: str = "Lead sheets for guitar"
    owner: str = ""
    style: str = "drop3"
    tuning: str = "standard"
    open_strings: str = "avoid"
    position: str | int = "auto"
    glossary: bool = True
    composer_index: bool = True
    no_page_turns: bool = True  # tunes of two or more pages start on a left-hand page
    workers: int = 0  # 0 = number of processors less one


@dataclass
class BookResult:
    path: Path
    pages: int
    songs: int
    skipped: list = field(default_factory=list)  # (title, reason)
    glossary_voicings: int = 0


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- fonts and text

def _font_dir() -> Path | None:
    base = Path(lilypond_exe()).resolve().parents[1] / "share" / "lilypond"
    hits = sorted(base.glob("*/fonts/otf"))
    return hits[-1] if hits else None


class _Fonts:
    """C059 (the lead sheets' text font) with Emmentaler for the flat and sharp signs."""

    def __init__(self):
        d = _font_dir()
        self.files = {}
        for key, name in (("roman", "C059-Roman.otf"), ("bold", "C059-Bold.otf"), ("italic", "C059-Italic.otf"),
                          ("acc", "emmentaler-20.otf")):
            f = d / name if d else None
            self.files[key] = str(f) if f and f.exists() else None
        fallback = {"roman": "tiro", "bold": "tibo", "italic": "tiit", "acc": None}
        self.font = {}
        for k, f in self.files.items():
            if f:
                self.font[k] = pymupdf.Font(fontfile=f)
            elif fallback[k]:
                self.font[k] = pymupdf.Font(fallback[k])
        self._pages = set()

    def name(self, k: str) -> str:
        return {"roman": "rbR", "bold": "rbB", "italic": "rbI", "acc": "rbA"}[k] if self.files.get(k) else \
            {"roman": "tiro", "bold": "tibo", "italic": "tiit"}[k]

    def register(self, page: pymupdf.Page):
        if page.xref in self._pages:
            return
        for k, f in self.files.items():
            if f:
                page.insert_font(fontname=self.name(k), fontfile=f)
        self._pages.add(page.xref)

    def _runs(self, text: str, k: str):
        acc_ok = "acc" in self.font
        run, out = "", []
        for ch in text:
            if ch in "♭♯" and acc_ok:
                if run:
                    out.append((run, k))
                    run = ""
                out.append((ch, "acc"))
            else:
                run += ch if acc_ok or ch not in "♭♯" else {"♭": "b", "♯": "#"}[ch]
        if run:
            out.append((run, k))
        return out

    def width(self, text: str, size: float, k: str = "roman") -> float:
        return sum(self.font[f].text_length(t, size * (0.85 if f == "acc" else 1)) for t, f in self._runs(text, k))

    def draw(self, page, x: float, y: float, text: str, size: float, k: str = "roman", color=INK, align="left"):
        self.register(page)
        w = self.width(text, size, k)
        if align == "center":
            x -= w / 2
        elif align == "right":
            x -= w
        for t, f in self._runs(text, k):
            s = size * (0.85 if f == "acc" else 1)
            page.insert_text((x, y - (size * 0.08 if f == "acc" else 0)), t, fontsize=s, fontname=self.name(f),
                             color=color)
            x += self.font[f].text_length(t, s)
        return w

    def fit(self, text: str, size: float, k: str, max_w: float) -> str:
        if self.width(text, size, k) <= max_w:
            return text
        while text and self.width(text + "…", size, k) > max_w:
            text = text[:-1]
        return text.rstrip() + "…"


def pretty_key(key: str, mode: str) -> str:
    return str(key).replace("b", "♭").replace("#", "♯") + ("m" if mode == "minor" else "")


def surname_key(composer: str) -> str:
    """Sort composers by the surname of the first one named: 'Antonio Carlos Jobim' files under J."""
    import re
    first = re.split(r",|&| and | / ", composer)[0].strip()
    words = first.split()
    return (words[-1] + " " + " ".join(words[:-1])).casefold() if words else ""


def sort_title(t: str) -> str:
    """Alphabetical position of a title: leading quotes and brackets are ignored ('Round Midnight -> R)."""
    import re
    return re.sub(r"^[^0-9a-z]+", "", t.casefold().strip())


# ---------------------------------------------------------------- engraving in cached groups

def _group_of(song_id: str) -> int:
    return zlib.crc32(song_id.encode()) % N_GROUPS


def _letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _group_source(items: list) -> str:
    parts = [f'\\version "{LY_VERSION}"', FRET_FUNCTION, "#(set-global-staff-size 17)", paper_block(False)]
    for i, (sid, arr) in enumerate(items):
        p = song_parts(arr, uid=_letters(i), marker=f"rbsong:{i}")
        parts.append(p["defs"])
        parts.append(f"\\bookpart {{\n\\header {{\n{p['header']}\n}}\n{p['score']}\n}}")
    return "\n".join(parts) + "\n"


def _song_ranges(pdf: Path, n: int) -> list[tuple[int, int]]:
    """(first page, page count) of each song in a group PDF, from the markers on the titles."""
    d = pymupdf.open(pdf)
    starts = {}
    for pno, page in enumerate(d):
        for link in page.get_links():
            uri = link.get("uri") or ""
            if uri.startswith("rbsong:"):
                starts.setdefault(int(uri.split(":")[1]), pno)
    total = d.page_count
    d.close()
    if len(starts) != n:
        raise RuntimeError(f"found {len(starts)} of {n} song starts in {pdf.name}")
    order = [starts[i] for i in range(n)]
    return [(s, (order[i + 1] if i + 1 < n else total) - s) for i, s in enumerate(order)]


def _engrave_group(items: list, out: Path) -> list[tuple[int, int]]:
    if not out.exists():
        tmp = out.with_suffix(".part.pdf")
        compile_pdf(_group_source(items), tmp, strict=False)
        tmp.replace(out)
    return _song_ranges(out, len(items))


# ---------------------------------------------------------------- chord glossary

FAMILY_ORDER = {"maj": 0, "min": 1, "dom": 2, "sus": 3, "hdim": 4, "dim": 5}
ROOT_LABELS = ["C", "C♯ / D♭", "D", "E♭ / D♯", "E", "F", "F♯ / G♭", "G", "A♭ / G♯", "A", "B♭ / A♯", "B"]


def collect_glossary(arrangements: list[Arrangement]) -> list:
    """Every distinct (chord as written, voicing) in the book, sorted by root, chord family, then position."""
    seen = {}
    for arr in arrangements:
        for _, ch, v in arr.voicings:
            ch = replace(ch, optional=False)
            key = (ch.symbol.strip("()"), tuple(v.frets))
            seen.setdefault(key, (ch, v))
    items = list(seen.values())

    def order(item):
        ch, v = item
        fr = v.fretted
        return (ch.root.pc, FAMILY_ORDER.get(ch.family(), 9), ch.bass is not None, len(ch.quality), ch.quality,
                str(ch.root), str(ch.bass or ""), min(fr) if fr else 0)
    return sorted(items, key=order)


def glossary_source(items: list, n_strings: int, style: str, tuning_txt: str) -> str:
    out = [f'\\version "{LY_VERSION}"', "#(set-global-staff-size 17)", paper_block(False),
           '\\markup \\fill-line { \\fontsize #7 \\bold "Chord glossary" }',
           f'\\markup \\fill-line {{ \\italic "Every chord voicing used in this book, by root: {STYLES[style]}, '
           f'{tuning_txt}." }}',
           '\\markup \\fill-line { \\italic "Numbers under the strings are chord degrees (R is the root); '
           'the number beside a diagram is its fret." }',
           '\\markup \\vspace #1']
    per_row, cell = 6, 20
    by_root: dict[int, list] = {}
    for ch, v in items:
        by_root.setdefault(ch.root.pc, []).append((ch, v))
    for pc in sorted(by_root):
        out.append(f'\\markup {{ \\column {{ \\vspace #1.2 \\fontsize #5 \\bold "{ROOT_LABELS[pc]}" '
                   f'\\vspace #0.2 \\override #\'(thickness . 1.5) \\draw-hline \\vspace #0.6 }} }}')
        group = by_root[pc]
        for r in range(0, len(group), per_row):
            cells = []
            for ch, v in group[r:r + per_row]:
                cells.append(f"\\with-dimension #X #'(0 . {cell}) \\hcenter-in #{cell} \\center-column {{ "
                             f"{chord_name_markup(ch)} \\vspace #0.3 {diagram_markup(v, n_strings)} }}")
            out.append(f"\\markup \\line {{ {' '.join(cells)} }}")
            out.append("\\markup \\vspace #0.8")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- page furniture

def draw_cover(page, fonts: _Fonts, opts: BookOptions, n_songs: int, tuning_txt: str, sources: list[str]):
    W, H = A4
    sh = page.new_shape()
    # double frame
    sh.draw_rect(pymupdf.Rect(28, 28, W - 28, H - 28))
    sh.finish(color=INK, width=1.6)
    sh.draw_rect(pymupdf.Rect(34, 34, W - 34, H - 34))
    sh.finish(color=INK, width=0.4)
    sh.commit()
    cx = W / 2
    fonts.draw(page, cx, 118, "THE GUITARIST'S", 11, "roman", GREY, "center")
    # title, scaled to fit the frame
    size = 46
    while fonts.width(opts.title, size, "bold") > W - 120 and size > 20:
        size -= 1
    fonts.draw(page, cx, 178, opts.title, size, "bold", INK, "center")
    if opts.subtitle:
        fonts.draw(page, cx, 214, opts.subtitle, 16, "italic", INK, "center")
    # ornament: rule with a diamond
    sh = page.new_shape()
    sh.draw_line((cx - 110, 240), (cx - 10, 240))
    sh.draw_line((cx + 10, 240), (cx + 110, 240))
    sh.finish(color=ACCENT, width=0.9)
    sh.draw_polyline([(cx, 234), (cx + 6, 240), (cx, 246), (cx - 6, 240), (cx, 234)])
    sh.finish(color=ACCENT, fill=ACCENT, width=0.5)
    sh.commit()
    _draw_neck(page, fonts, cx, 420)
    fonts.draw(page, cx, 600, f"{n_songs:,} tunes".replace(",", " ") + "  ·  melody, tab and jazz chord voicings",
               12.5, "roman", INK, "center")
    fonts.draw(page, cx, 621, f"{STYLES[opts.style]} voicings  ·  {tuning_txt}", 11, "italic", GREY, "center")
    if opts.owner:
        fonts.draw(page, cx, 672, opts.owner, 15, "italic", INK, "center")
    fonts.draw(page, cx, H - 70, date.today().strftime("%B %Y"), 10, "roman", GREY, "center")
    if sources:
        fonts.draw(page, cx, H - 54, "Tunes from " + ", ".join(sources), 8, "italic", GREY, "center")


def _draw_neck(page, fonts: _Fonts, cx: float, cy: float):
    """A stretch of fretboard (frets 7 to 11) holding a Cmaj7 voicing, each note labelled with its degree."""
    w, h = 330, 120
    x0, y0 = cx - w / 2, cy - h / 2
    frets = 5
    sh = page.new_shape()
    sh.draw_rect(pymupdf.Rect(x0, y0, x0 + w, y0 + h))
    sh.finish(color=None, fill=(0.97, 0.95, 0.91), width=0)
    for i in range(frets + 1):
        x = x0 + i * w / frets
        sh.draw_line((x, y0), (x, y0 + h))
        sh.finish(color=(0.55, 0.52, 0.47), width=1.2 if i else 0.8)
    for s in range(6):  # high E at the top
        y = y0 + s * h / 5
        sh.draw_line((x0 - 14, y), (x0 + w + 14, y))
        sh.finish(color=INK, width=0.5 + s * 0.28)
    for i in (0,):  # the 7th-fret inlay
        x = x0 + (i + 0.5) * w / frets
        sh.draw_circle((x, y0 + 2.5 * h / 5), 5)
        sh.finish(color=None, fill=(0.85, 0.82, 0.76), width=0)
    sh.commit()
    # (string counted from the top/high E, fret, degree): C E G B on the D G B E strings
    notes = [(3, 10, "R"), (2, 9, "3"), (1, 8, "5"), (0, 7, "7")]
    first = 6  # the leftmost fret space shown is fret 7
    sh = page.new_shape()
    for s, f, _ in notes:
        x = x0 + (f - first - 0.5) * w / frets
        sh.draw_circle((x, y0 + s * h / 5), 11)
        sh.finish(color=None, fill=ACCENT, width=0)
    sh.commit()
    for s, f, d in notes:
        x = x0 + (f - first - 0.5) * w / frets
        fonts.draw(page, x, y0 + s * h / 5 + 4.2, d, 11.5, "bold", (1, 1, 1), "center")
    fonts.draw(page, cx, y0 + h + 28, "C maj7", 12, "italic", GREY, "center")
    fonts.draw(page, x0 + 0.5 * w / frets, y0 + h + 14, "7", 8, "roman", GREY, "center")


def number_pages(doc: pymupdf.Document, fonts: _Fonts, skip: set[int]):
    W, H = A4
    for pno in range(doc.page_count):
        if pno in skip:
            continue
        page = doc[pno]
        n = pno + 1
        x, align = (W - 40, "right") if n % 2 else (40, "left")
        fonts.draw(page, x, H - 22, str(n), 10, "roman", GREY, align)


def notes_page(page, fonts: _Fonts):
    """A blank page that keeps the next tune on facing pages: empty staves for the player's own notes."""
    W, _ = A4
    sh = page.new_shape()
    y = 90
    for _ in range(11):
        for k in range(5):
            sh.draw_line((50, y + k * 6), (W - 50, y + k * 6))
        y += 62
    sh.finish(color=(0.78, 0.79, 0.82), width=0.4)
    sh.commit()
    fonts.draw(page, W / 2, 60, "Notes", 12, "italic", GREY, "center")


# ---------------------------------------------------------------- index pages

ROW = 13.2
TOP, BOTTOM = 92, 800


def index_rows(entries: list, by: str) -> list:
    """entries: dicts with title, composer, key, page. Returns rows: ('head', text) or ('entry', left, right, page)."""
    rows, cur = [], None
    if by == "title":
        for e in sorted(entries, key=lambda e: sort_title(e["title"])):
            letter = (sort_title(e["title"])[:1] or "#").upper()
            letter = letter if letter.isalpha() else "#"
            if letter != cur:
                rows.append(("head", letter))
                cur = letter
            rows.append(("entry", e["title"], e["key"], e["page"]))
    else:
        groups: dict[str, list] = {}
        for e in entries:
            groups.setdefault(e["composer"].strip() or "Composer unknown", []).append(e)
        for comp in sorted(groups, key=lambda c: (c == "Composer unknown", surname_key(c))):
            rows.append(("head", comp))
            for e in sorted(groups[comp], key=lambda e: sort_title(e["title"])):
                rows.append(("entry", e["title"], e["key"], e["page"]))
    return rows


def _layout(rows: list) -> list[list[list]]:
    """Pages of two columns; a heading never ends a column."""
    per_col = int((BOTTOM - TOP) // ROW)
    first_per_col = int((BOTTOM - TOP - 40) // ROW)
    pages, cols, col = [], [], []
    limit = first_per_col
    for i, r in enumerate(rows):
        need = 2 if r[0] == "head" else 1
        if len(col) + need > limit:
            cols.append(col)
            col = []
            if len(cols) == 2:
                pages.append(cols)
                cols = []
                limit = per_col
        if r[0] == "head" and col:
            col.append(("gap",))
        col.append(r)
    if col:
        cols.append(col)
    if cols:
        pages.append(cols)
    return pages


def index_page_count(rows: list) -> int:
    return len(_layout(rows))


def draw_index(doc, first_pno: int, rows: list, fonts: _Fonts, heading: str, song_start: dict):
    W, _ = A4
    margin, gutter = 46, 26
    col_w = (W - 2 * margin - gutter) / 2
    for pi, cols in enumerate(_layout(rows)):
        page = doc[first_pno + pi]
        y0 = TOP
        if pi == 0:
            fonts.draw(page, W / 2, 70, heading, 24, "bold", INK, "center")
            y0 = TOP + 40
        for ci, col in enumerate(cols):
            x = margin + ci * (col_w + gutter)
            y = y0
            for r in col:
                if r[0] == "gap":
                    y += ROW * 0.5
                    continue
                if r[0] == "head":
                    fonts.draw(page, x, y + 2, fonts.fit(r[1], 12.5, "bold", col_w), 12.5, "bold", ACCENT)
                    y += ROW * 1.25
                    continue
                _, title, key, pg = r
                num = str(pg)
                nw = fonts.width(num, 9.5)
                kw = fonts.width(key, 8) if key else 0
                t = fonts.fit(title, 9.5, "roman", col_w - nw - kw - 26)
                tw = fonts.draw(page, x, y, t, 9.5, "roman", INK)
                if key:
                    fonts.draw(page, x + tw + 4, y, key, 8, "italic", GREY)
                fonts.draw(page, x + col_w, y, num, 9.5, "roman", INK, "right")
                # dotted leader
                lx0, lx1 = x + tw + kw + 10, x + col_w - nw - 5
                if lx1 > lx0:
                    sh = page.new_shape()
                    xx = lx0
                    while xx < lx1:
                        sh.draw_circle((xx, y - 2.4), 0.45)
                        xx += 3.6
                    sh.finish(color=GREY, fill=GREY, width=0)
                    sh.commit()
                target = song_start.get(pg)
                if target is not None:
                    page.insert_link({"kind": pymupdf.LINK_GOTO, "page": target,
                                      "from": pymupdf.Rect(x, y - 9, x + col_w, y + 3), "to": pymupdf.Point(0, 0)})
                y += ROW


# ---------------------------------------------------------------- the whole book

@dataclass
class Engraved:
    songs: list  # (song id, data) sorted by title
    arrs: dict  # song id -> Arrangement
    ranges: dict  # song id -> (group pdf, first page, page count)
    skipped: list
    tuning_txt: str
    glossary: list
    glossary_pdf: Path | None


def engrave_songs(songs: list[tuple[str, dict]], opts: BookOptions, cache_dir: Path, progress=None,
                  cancelled=lambda: False, glossary: bool = False) -> Engraved:
    """Arrange and engrave songs in cached groups. progress(done, total, message); total is len(songs) * 3."""
    progress = progress or (lambda *a: None)
    cache_dir = Path(cache_dir) / "songbook"
    cache_dir.mkdir(parents=True, exist_ok=True)
    songs = sorted(songs, key=lambda s: sort_title(s[1].get("title", "")))
    total = len(songs)

    # 1. arrangement (chords and fingering) for every song
    arrs: dict[str, Arrangement] = {}
    skipped = []
    for i, (sid, data) in enumerate(songs):
        if cancelled():
            raise Cancelled()
        if i % 10 == 0:
            progress(i, total * 3, f"Choosing voicings and fingering: {data.get('title', sid)}")
        try:
            arrs[sid] = arrange(from_dict(data), opts.style, opts.tuning, "guitar", opts.open_strings, opts.position)
        except Exception as e:  # a song that can't be arranged is left out and reported
            skipped.append((data.get("title", sid), str(e).splitlines()[0] if str(e) else type(e).__name__))
    if not arrs:
        raise RuntimeError("None of the chosen songs could be arranged.")
    tuning_txt = f"{opts.tuning.replace('-', ' ').capitalize()} tuning (" + " ".join(
        NOTE_NAMES[m % 12] for m in next(iter(arrs.values())).tuning) + ")"

    # 2. engrave groups in parallel (cached)
    groups: dict[int, list] = {}
    for sid, data in songs:
        if sid in arrs:
            groups.setdefault(_group_of(sid), []).append((sid, data))
    opt_key = [opts.style, opts.tuning, opts.open_strings, str(opts.position), BOOK_VERSION, LY_VERSION]
    jobs = []
    for g, members in groups.items():
        members.sort(key=lambda m: m[0])
        h = hashlib.sha1(json.dumps([opt_key, [(sid, d.get("bars"), d.get("key"), d.get("mode"), d.get("title"),
                                                  d.get("composer"), d.get("style"), d.get("pickup"),
                                                  d.get("time"), d.get("line_breaks")) for sid, d in members]],
                                    sort_keys=True, default=str).encode()).hexdigest()[:16]
        jobs.append((g, members, cache_dir / f"g{g:02d}-{h}.pdf"))
    workers = opts.workers or max(1, (os.cpu_count() or 2) - 1)
    ranges: dict[str, tuple[Path, int, int]] = {}
    done_songs = 0
    gl_items = collect_glossary(list(arrs.values())) if glossary else []
    gl_pdf = None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        if gl_items:
            src = glossary_source(gl_items, len(next(iter(arrs.values())).tuning), opts.style, tuning_txt)
            gh = hashlib.sha1(src.encode()).hexdigest()[:16]
            gl_pdf = cache_dir / f"glossary-{gh}.pdf"
            if not gl_pdf.exists():
                futs[ex.submit(compile_pdf, src, gl_pdf, None, False)] = ("glossary", None, None)
        for g, members, pdf in jobs:
            futs[ex.submit(_engrave_group, [(sid, arrs[sid]) for sid, _ in members], pdf)] = ("group", members, pdf)
        progress(total, total * 3, "Engraving lead sheets…")
        for fut in as_completed(futs):
            kind, members, pdf = futs[fut]
            if cancelled():
                for f in futs:
                    f.cancel()
                raise Cancelled()
            try:
                res = fut.result()
            except Exception as e:
                if kind == "glossary":
                    skipped.append(("Chord glossary", str(e).strip().splitlines()[-1][:200]))
                    gl_pdf = None
                    continue
                # engrave this group's songs one by one so a single bad song doesn't take others with it
                res = None
                for sid, d in members:
                    one = cache_dir / f"one-{sid}-{pdf.stem.split('-')[1]}.pdf"
                    try:
                        (r1,) = _engrave_group([(sid, arrs[sid])], one)
                        ranges[sid] = (one, *r1)
                    except Exception as e1:
                        skipped.append((d.get("title", sid), str(e1).strip().splitlines()[-1][:200]))
            if kind == "group":
                if res is not None:
                    for (sid, _), (start, count) in zip(members, res):
                        ranges[sid] = (pdf, start, count)
                done_songs += len(members)
                progress(total + 2 * done_songs, total * 3, f"Engraved {done_songs} of {len(arrs)} tunes")
    return Engraved(songs, arrs, ranges, skipped, tuning_txt, gl_items, gl_pdf)


def safe_filename(title: str) -> str:
    bad = '<>:"/\\|?*'
    name = "".join("-" if c in bad or ord(c) < 32 else c for c in title).strip().rstrip(".")
    return (name or "Untitled")[:120]


def export_pdfs(songs: list[tuple[str, dict]], opts: BookOptions, folder: Path, cache_dir: Path, progress=None,
                cancelled=lambda: False) -> tuple[list[Path], list]:
    """One PDF per song in folder, named by title (for MobileSheets and similar apps).

    Title, composer and key are also written into each PDF's properties. Returns (files written, skipped).
    """
    progress = progress or (lambda *a: None)
    eng = engrave_songs(songs, opts, cache_dir, progress, cancelled)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written, used = [], set()
    open_docs = {}
    try:
        for sid, d in eng.songs:
            if sid not in eng.ranges:
                continue
            if cancelled():
                raise Cancelled()
            pdf, start, count = eng.ranges[sid]
            if pdf not in open_docs:
                open_docs[pdf] = pymupdf.open(pdf)
            name = safe_filename(d.get("title", sid))
            stem, k = name, 2
            while stem.casefold() in used:  # two tunes with the same title
                stem = f"{name} ({k})"
                k += 1
            used.add(stem.casefold())
            one = pymupdf.open()
            one.insert_pdf(open_docs[pdf], from_page=start, to_page=start + count - 1, links=False, annots=False)
            key = pretty_key(d.get("key", ""), d.get("mode", "major")).replace("♭", "b").replace("♯", "#")
            one.set_metadata({"title": d.get("title", ""), "author": d.get("composer", ""),
                              "subject": f"Key: {key}", "keywords": ", ".join(
                                  x for x in (d.get("style", ""), f"key {key}", STYLES[opts.style]) if x),
                              "creator": "Real Book Guitar"})
            target = folder / f"{stem}.pdf"
            one.save(str(target), garbage=3, deflate=True)
            one.close()
            written.append(target)
            progress(len(eng.songs) * 3, len(eng.songs) * 3, f"Saved {len(written)} PDFs")
    finally:
        for doc in open_docs.values():
            doc.close()
    return written, eng.skipped


def build_songbook(songs: list[tuple[str, dict]], opts: BookOptions, out_pdf: Path, cache_dir: Path,
                   progress=None, cancelled=lambda: False) -> BookResult:
    """songs: (song id, song data). progress(done, total, message) is called as work advances."""
    t0 = time.time()
    progress = progress or (lambda *a: None)
    eng = engrave_songs(songs, opts, cache_dir, progress, cancelled, glossary=opts.glossary)
    songs, ranges, skipped, tuning_txt = eng.songs, eng.ranges, eng.skipped, eng.tuning_txt
    gl_items, gl_pdf = eng.glossary, eng.glossary_pdf
    total = len(songs)

    # 3. assemble: cover, index, songs, glossary, composer index
    progress(total * 3 - 1, total * 3, "Putting the book together…")
    fonts = _Fonts()
    book = pymupdf.open()
    placed = [(sid, d) for sid, d in songs if sid in ranges]
    entries = [{"title": d.get("title", sid), "composer": d.get("composer", ""),
                "key": pretty_key(d.get("key", ""), d.get("mode", "major")), "page": 0} for sid, d in placed]
    n_index = index_page_count(index_rows(entries, "title"))
    book.new_page(width=A4[0], height=A4[1])  # cover
    for _ in range(n_index):
        book.new_page(width=A4[0], height=A4[1])
    blank = set()
    sources = {}
    open_docs = {}
    for e, (sid, d) in zip(entries, placed):
        pdf, start, count = ranges[sid]
        if opts.no_page_turns and count >= 2 and (book.page_count + 1) % 2 == 1:
            notes_page(book.new_page(width=A4[0], height=A4[1]), fonts)
            blank.add(book.page_count - 1)
        e["page"] = book.page_count + 1
        if pdf not in open_docs:
            open_docs[pdf] = pymupdf.open(pdf)
        book.insert_pdf(open_docs[pdf], from_page=start, to_page=start + count - 1, links=False, annots=False)
        if d.get("openbook"):
            sources["OpenBook (github.com/veltzer/openbook)"] = 1
        elif d.get("book"):
            sources[d["book"]] = 1
    for doc in open_docs.values():
        doc.close()
    gl_start = None
    if gl_pdf and gl_pdf.exists():
        if book.page_count % 2 == 1:  # glossary opens on a right-hand page
            notes_page(book.new_page(width=A4[0], height=A4[1]), fonts)
            blank.add(book.page_count - 1)
        gl_start = book.page_count
        with pymupdf.open(gl_pdf) as g:
            book.insert_pdf(g, links=False, annots=False)
    comp_start = None
    if opts.composer_index:
        comp_rows = index_rows(entries, "composer")
        comp_start = book.page_count
        for _ in range(index_page_count(comp_rows)):
            book.new_page(width=A4[0], height=A4[1])
    song_start = {e["page"]: e["page"] - 1 for e in entries}
    draw_cover(book[0], fonts, opts, len(entries), tuning_txt, list(sources))
    rows = index_rows(entries, "title")
    extra = []
    if gl_start is not None:
        extra.append(("Chord glossary", gl_start + 1))
    if comp_start is not None:
        extra.append(("Index by composer", comp_start + 1))
    draw_index(book, 1, rows, fonts, "Index", song_start)
    if extra:  # where the back matter is, under the heading of the first index page
        p = book[1]
        txt = "   ·   ".join(f"{t}: page {n}" for t, n in extra)
        fonts.draw(p, A4[0] / 2, 88, txt, 9.5, "italic", GREY, "center")
    if comp_start is not None:
        draw_index(book, comp_start, comp_rows, fonts, "Index by composer", song_start)
    number_pages(book, fonts, skip={0})
    # bookmarks: index, each tune under its letter, glossary, composer index
    toc = [[1, "Index", 2]]
    cur = None
    for e in sorted(entries, key=lambda e: sort_title(e["title"])):
        letter = (sort_title(e["title"])[:1] or "#").upper()
        if letter != cur:
            toc.append([1, letter if letter.isalpha() else "#", e["page"]])
            cur = letter
        toc.append([2, e["title"], e["page"]])
    if gl_start is not None:
        toc.append([1, "Chord glossary", gl_start + 1])
    if comp_start is not None:
        toc.append([1, "Index by composer", comp_start + 1])
    book.set_toc(toc)
    book.set_metadata({"title": opts.title, "subject": opts.subtitle, "creator": "Real Book Guitar",
                       "author": opts.owner})
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_pdf.with_name(out_pdf.stem + ".saving.pdf")
    book.save(str(tmp), garbage=3, deflate=True)
    n_pages = book.page_count
    book.close()
    tmp.replace(out_pdf)
    progress(total * 3, total * 3, f"Done in {time.time() - t0:.0f} s")
    return BookResult(out_pdf, n_pages, len(entries), skipped, len(gl_items))


def page_count_estimate(n_songs: int) -> int:
    return math.ceil(n_songs * 2.6)
