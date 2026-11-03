"""Import a Real Book PDF: plan which pages hold which song, then read each song into the library."""
from __future__ import annotations

import csv
import re
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..omr.kern2song import staves_to_song
from ..omr.staves import crop, find_staves
from .library import Library, slug


@dataclass
class PlannedSong:
    title: str
    first_page: int  # 1-based PDF page numbers
    last_page: int


# ---------------------------------------------------------------- planning

def plan_from_bookmarks(pdf_path: str) -> list[PlannedSong]:
    import pymupdf

    doc = pymupdf.open(pdf_path)
    toc = [(lvl, title.strip(), page) for lvl, title, page in doc.get_toc(simple=True) if page >= 1]
    if not toc:
        return []
    # use the deepest level that has many entries (song titles), not section headings like "A", "B"
    levels = {}
    for lvl, _, _ in toc:
        levels[lvl] = levels.get(lvl, 0) + 1
    level = max(levels, key=lambda lv: (levels[lv], lv))
    entries = [(t, p) for lvl, t, p in toc if lvl == level]
    entries.sort(key=lambda e: e[1])
    out = []
    for i, (title, page) in enumerate(entries):
        nxt = entries[i + 1][1] if i + 1 < len(entries) else doc.page_count + 1
        last = max(page, nxt - 1)
        out.append(PlannedSong(title, page, min(last, page + 3)))  # cap: a song rarely spans more than 4 pages
    return out


def plan_one_per_page(pdf_path: str, first: int = 1, last: int | None = None) -> list[PlannedSong]:
    import pymupdf

    n = pymupdf.open(pdf_path).page_count
    last = min(last or n, n)
    return [PlannedSong(f"Page {p}", p, p) for p in range(first, last + 1)]


def plan_from_index(index_path: str, page_offset: int, page_count: int) -> list[PlannedSong]:
    """CSV or text index with a title and a book page number per line, e.g. 'A Foggy Day,6'.
    page_offset converts book page numbers to PDF page numbers (PDF page = book page + offset)."""
    rows = []
    with open(index_path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cells = next(csv.reader([line])) if "," in line or ";" in line else re.split(r"\s{2,}|\t", line)
        if len(cells) == 1:
            m = re.match(r"(.+?)[\s.]+(\d+)\s*$", line)
            if not m:
                continue
            cells = [m.group(1), m.group(2)]
        title = cells[0].strip()
        nums = [c for c in cells[1:] if re.fullmatch(r"\s*\d+\s*", c)]
        if not title or not nums:
            continue
        rows.append((title, int(nums[0]) + page_offset))
    rows = [r for r in rows if 1 <= r[1] <= page_count]
    rows.sort(key=lambda r: r[1])
    out = []
    for i, (title, page) in enumerate(rows):
        nxt = rows[i + 1][1] if i + 1 < len(rows) else page + 1
        out.append(PlannedSong(title, page, max(page, min(nxt - 1, page + 3))))
    return out


# ---------------------------------------------------------------- reading pages

def native_dpi(page) -> float:
    """Resolution of the scan inside a PDF page, so we read it without resampling."""
    imgs = page.get_images(full=True)
    if len(imgs) == 1:
        w = imgs[0][2]
        dpi = w / (page.rect.width / 72.0)
        return max(90.0, min(300.0, dpi))
    return 150.0


def page_image(doc, page_no: int) -> Image.Image:
    import pymupdf

    page = doc[page_no - 1]
    pix = page.get_pixmap(dpi=int(round(native_dpi(page))), colorspace=pymupdf.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def song_id_for(book_id: str, ps: PlannedSong) -> str:
    return f"{book_id}-{ps.first_page:04d}"


class ImportJob:
    """Reads planned songs one by one. Call run(); it reports progress through callbacks."""

    def __init__(self, library: Library, pdf_path: str, book_title: str, plan: list[PlannedSong]):
        self.lib = library
        self.pdf = str(pdf_path)
        self.book_id = slug(book_title)
        self.book_title = book_title
        self.plan = plan
        self.cancelled = False

    def save_book(self):
        self.lib.save_book(self.book_id, {
            "title": self.book_title, "pdf": self.pdf,
            "plan": [{"title": p.title, "first_page": p.first_page, "last_page": p.last_page} for p in self.plan],
        })

    def log(self, msg: str) -> None:
        try:
            with open(self.lib.root / "import.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except OSError:
            pass

    def run(self, on_progress=lambda done, total, title, eta: None, on_song=lambda song_id: None, recognizer=None,
            on_line=lambda page, line, lines: None) -> dict:
        """Read every planned song not yet in the library. Returns a summary.

        on_progress(done, total, title, eta_seconds_or_None) before each song,
        on_line(page, line_number, lines_on_page) before each line of music, so the window shows life
        every couple of seconds. Pages without music (covers, contents, blank pages) are skipped quickly.
        A song that fails is logged and skipped; the import carries on.
        """
        import traceback

        import pymupdf

        from ..omr.recognize import default_recognizer

        self.save_book()
        book = self.lib.load_book(self.book_id) or {}
        no_music = set(book.get("no_music", []))
        summary = {"imported": 0, "no_music": 0, "failed": 0, "failed_titles": []}
        self.log(f"import of '{self.book_title}' ({self.pdf}): {len(self.plan)} songs planned")
        rec = recognizer or default_recognizer()
        doc = pymupdf.open(self.pdf)
        todo = [p for p in self.plan
                if not self.lib.has_song(song_id_for(self.book_id, p)) and p.first_page not in no_music]
        pages_total = sum(p.last_page - p.first_page + 1 for p in todo)
        pages_done = 0
        music_pages, music_secs = 0, 0.0  # estimate from pages that actually had music, not quick blank pages
        for k, ps in enumerate(todo):
            if self.cancelled:
                break
            eta = music_secs / music_pages * (pages_total - pages_done) if music_pages else None
            on_progress(k, len(todo), ps.title, eta)
            sid = song_id_for(self.book_id, ps)
            try:
                kerns, systems = [], []
                for page_no in range(ps.first_page, ps.last_page + 1):
                    if self.cancelled:
                        break
                    t_page = time.time()
                    img = page_image(doc, page_no)
                    staves = find_staves(img)
                    for li, st in enumerate(staves):
                        if self.cancelled:
                            break
                        on_line(page_no, li + 1, len(staves))
                        c = crop(img, st)
                        n = len(systems)
                        c.save(self.lib.crop_path(sid, n))
                        kerns.append(rec.kern(c))
                        systems.append({"page": page_no, "box": list(st.crop_box),
                                        "crop": self.lib.crop_path(sid, n).name})
                    pages_done += 1
                    if staves and not self.cancelled:
                        music_pages += 1
                        music_secs += time.time() - t_page
                if self.cancelled:
                    break
                if not kerns:
                    no_music.add(ps.first_page)
                    summary["no_music"] += 1
                    self.log(f"pages {ps.first_page}-{ps.last_page} ('{ps.title}'): no music found, skipped")
                    continue
                song = staves_to_song(kerns, ps.title, systems)
                song.update({"book": self.book_id, "pages": list(range(ps.first_page, ps.last_page + 1))})
                self.lib.save_song(sid, song)
                summary["imported"] += 1
                self.log(f"'{ps.title}' (pages {ps.first_page}-{ps.last_page}): {len(systems)} lines, "
                         f"{len(song['bars'])} bars")
                on_song(sid)
            except Exception:
                summary["failed"] += 1
                summary["failed_titles"].append(ps.title)
                self.log(f"'{ps.title}' (pages {ps.first_page}-{ps.last_page}) FAILED:\n{traceback.format_exc()}")
        book = self.lib.load_book(self.book_id) or {}
        book["no_music"] = sorted(no_music)
        self.lib.save_book(self.book_id, book)
        self.log(f"finished: {summary}" + (" (stopped by user)" if self.cancelled else ""))
        on_progress(len(todo), len(todo), "", 0)
        return summary
