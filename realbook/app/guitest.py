"""End-to-end test of the real window: import a book, review it, change options quickly, edit bars.

Run:  RealBookGuitar.exe --gui-selftest      (or python RealBookGuitar.py --gui-selftest)
Uses a temporary library, never the user's. Writes gui-selftest.txt next to the program and exits 0 (pass) / 1.
It exists because these failures only show up when the window is actually driven:
overlapping background jobs, long first engraving, empty screens.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path


def _engrave_book(pdf: Path):
    """Make a 3-page 'book': a cover without music, then two pages of the test tune as a lead sheet."""
    import subprocess

    import pymupdf

    from ..engraver import lilypond_exe
    from ..render_ly import _RUN, ly_dur, ly_pitch
    from ..song import load

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    fx = base / "fixtures" / "test_tune.json"
    if not fx.exists():
        fx = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "test_tune.json"
    s = load(fx)
    mel = []
    for i, b in enumerate(s.bars):
        mel.append(" ".join(f"r{ly_dur(n.beats)}" if n.pitch is None else ly_pitch(n.pitch, 0) + ly_dur(n.beats)
                            + ("~" if n.tie else "") for n in b.notes) + (" \\break" if s.breaks_after(i) else " |"))
    ly = ('\\version "2.25.12"\n\\paper { indent = 0 tagline = ##f }\n'
          f'\\score {{ \\new Staff {{ \\key f \\major \\time 4/4 {" ".join(mel)} }} \\layout {{}} }}\n')
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "t.ly"
        src.write_text(ly, encoding="utf-8")
        subprocess.run([lilypond_exe(), "-o", str(Path(td) / "t"), str(src)], **_RUN)
        music = pymupdf.open(str(Path(td) / "t.pdf"))
        book = pymupdf.open()
        cover = book.new_page()
        cover.insert_text((150, 300), "TEST BOOK", fontsize=40)
        book.insert_pdf(music)
        book.insert_pdf(music)
        book.save(str(pdf))


def run() -> int:
    from PySide6.QtWidgets import QApplication

    log: list[str] = []
    t_start = time.time()

    def note(msg):
        log.append(f"{time.time() - t_start:7.1f}s  {msg}")
        print(log[-1], flush=True)

    out = Path(sys.executable).parent / "gui-selftest.txt" if getattr(sys, "frozen", False) else Path("gui-selftest.txt")
    ok = False
    tmp = Path(tempfile.mkdtemp(prefix="rbg-guitest-"))
    os.environ["RBG_SETTINGS_DIR"] = str(tmp / "settings")
    os.environ["RBG_LIBRARY"] = str(tmp / "library")
    try:
        from . import main as appmain
        from .importer import plan_one_per_page

        app = QApplication.instance() or QApplication(sys.argv)
        app.setStyleSheet(appmain.STYLE_SHEET)

        def pump(seconds: float, until=None):
            end = time.time() + seconds
            while time.time() < end:
                app.processEvents()
                if until and until():
                    return True
                time.sleep(0.02)
            return bool(until and until())

        pdf = tmp / "book.pdf"
        _engrave_book(pdf)
        note("test book engraved")
        w = appmain.MainWindow()
        w.resize(1300, 850)
        w.show()
        pump(1)
        plan = plan_one_per_page(str(pdf))
        w.start_import(str(pdf), "Test Book", plan)
        assert w.imp_panel.isVisible(), "import panel not shown"
        assert pump(5, lambda: "line" in w.imp_detail.text()), "no line-by-line progress within 5 s"
        note(f"progress shown: {w.imp_detail.text()!r}")
        assert pump(600, lambda: not w.import_thread.isRunning()), "import did not finish in 10 minutes"
        pump(1)
        entries = w.lib.entries()
        note(f"import finished: {w.imp_detail.text()!r}; songs: {[e.title for e in entries]}")
        assert len(entries) == 2, f"expected 2 songs (cover skipped), got {len(entries)}"

        # open the first song: review screen must show scans and previews
        w.list.setCurrentRow(0)
        pump(1)
        rv = w.review_view
        assert rv.panels, "review screen shows no lines"
        assert pump(240, lambda: all(p.preview.pixmap() is not None and not p.preview.pixmap().isNull()
                                     for p in rv.panels)), "previews not drawn within 4 minutes"
        note(f"review: {len(rv.panels)} lines with scans and previews")

        # hammer the lead sheet options: each change starts a new engraving while the last is running
        w.tabs.setCurrentIndex(0)
        sv = w.song_view
        for style_i, key_i in [(0, 3), (1, 5), (2, 7), (3, 1), (2, 0)]:
            sv.style.setCurrentIndex(style_i)
            sv.key.setCurrentIndex(key_i)
            pump(0.3)
        assert pump(300, lambda: sv.doc.pageCount() > 0 and not sv.busy()), "lead sheet never appeared"
        note(f"lead sheet: {sv.doc.pageCount()} page(s), style {sv.style.currentData()}, key {sv.key.currentData()}")

        # edit bars quickly on the review screen (each edit re-draws previews while earlier ones run)
        w.tabs.setCurrentIndex(1)
        pump(0.5)
        t = rv.panels[0].table
        for txt in ("Fmaj7", "F6", "Fmaj7 D7", "Fmaj7"):
            t.item(0, 1).setText(txt)
            pump(0.2)
        t.item(0, 2).setText("4r 4A4 4C5 4E5")
        assert pump(240, lambda: not rv.busy()), "review previews stuck"
        song = w.lib.load_song(rv.song_id)
        assert [c["symbol"] for c in song["bars"][0]["chords"]] == ["Fmaj7"], "edit not saved"
        note("edits saved and previews redrawn")

        # switch songs back and forth while jobs run
        for r in (1, 0, 1, 0):
            w.list.setCurrentRow(r)
            pump(0.3)
        assert pump(300, lambda: not rv.busy() and not sv.busy()), "jobs did not settle after switching songs"
        note("switching songs while jobs run: ok")
        w.close()
        pump(1)
        ok = True
    except Exception:
        log.append(traceback.format_exc())
        print(log[-1], flush=True)
    out.write_text("\n".join(log) + ("\nPASS\n" if ok else "\nFAIL\n"), encoding="utf-8")
    return 0 if ok else 1
