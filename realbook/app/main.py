"""Real Book Guitar: the desktop app."""
from __future__ import annotations

import faulthandler
import logging
import os
import shutil
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QtMsgType, QTimer, QUrl, qInstallMessageHandler
from PySide6.QtGui import QAction, QDesktopServices, QFont, QImage, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from ..voicings import STYLES, TUNINGS
from . import editing
from .importer import ImportJob, PlannedSong, plan_from_bookmarks, plan_from_index, plan_one_per_page
from .library import Library, check_writable, load_settings, save_settings, settings_dir
from .workers import KEEPER, ImportThread, PreviewJob, RenderJob

APP_NAME = "Real Book Guitar"
KEYS = ["Original", "C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
TUNING_NAMES = {"standard": "Standard (E A D G B E)", "half-step-down": "Half step down (Eb)",
                "drop-d": "Drop D", "all-fourths": "All fourths (E A D G C F)"}
STYLE_NAMES = {"shell": "Shells", "drop2": "Drop-2", "drop3": "Drop-3", "rootless": "Rootless"}

STYLE_SHEET = """
QWidget { font-size: 10pt; }
QLabel#banner { background: #fff4d6; color: #5a4300; border: 1px solid #f0d58a; border-radius: 4px; padding: 6px 10px; }
QLabel#bannerOk { background: #e6f4ea; color: #1d5a32; border: 1px solid #a8d5b5; border-radius: 4px; padding: 6px 10px; }
QLabel#error { background: #fde8e6; color: #7a1d16; border: 1px solid #f1b5af; border-radius: 4px; padding: 6px 10px; }
QLabel.caption { color: #5b6676; font-size: 9pt; }
QFrame#system { border: 1px solid #dde2ea; border-radius: 6px; background: palette(base); }
QListWidget::item { padding: 4px 2px; }
QFrame#importPanel { background: #eaf2fb; border: 1px solid #b7cde6; border-radius: 6px; }
QLabel#importTitle { font-size: 12pt; font-weight: 600; }
"""


def _combo(items: dict | list, current=None) -> QComboBox:
    c = QComboBox()
    if isinstance(items, dict):
        for k, v in items.items():
            c.addItem(v, k)
    else:
        for k in items:
            c.addItem(str(k), k)
    if current is not None:
        i = c.findData(current)
        if i >= 0:
            c.setCurrentIndex(i)
    return c


# ====================================================================== lead sheet view

class SongView(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.song_id = None
        self.song = None
        self.job = None
        self._request = None
        s = win.settings
        self.style = _combo(STYLE_NAMES, s["style"])
        self.key = _combo(KEYS, "Original")
        self.tuning = _combo(TUNING_NAMES, s["tuning"])
        self.open_strings = _combo({"avoid": "Avoid open strings", "allow": "Allow open strings"}, s["open_strings"])
        self.position = _combo({"auto": "Position: auto", **{str(p): f"Position {p}" for p in range(1, 13)}},
                               str(s["position"]))
        for w in (self.style, self.key, self.tuning, self.open_strings, self.position):
            w.currentIndexChanged.connect(self.render)
        self.export_btn = QPushButton("Save PDF…")
        self.print_btn = QPushButton("Print…")
        self.export_btn.clicked.connect(self.export_pdf)
        self.print_btn.clicked.connect(self.print_pdf)

        bar = QHBoxLayout()
        for label, w in (("Voicings", self.style), ("Key", self.key), ("Tuning", self.tuning)):
            bar.addWidget(QLabel(label))
            bar.addWidget(w)
        bar.addWidget(self.open_strings)
        bar.addWidget(self.position)
        bar.addStretch(1)
        bar.addWidget(self.export_btn)
        bar.addWidget(self.print_btn)

        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.setTextFormat(Qt.RichText)
        self.banner.linkActivated.connect(lambda _: win.tabs.setCurrentIndex(1))
        self.banner.hide()
        self.status = QLabel("")
        self.status.setProperty("class", "caption")

        self.doc = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setDocument(self.doc)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.banner)
        lay.addWidget(self.status)
        lay.addWidget(self.view, 1)
        self.pdf_path = None

    def options(self) -> dict:
        pos = self.position.currentData()
        return {"style": self.style.currentData(), "key": self.key.currentData(), "tuning": self.tuning.currentData(),
                "open_strings": self.open_strings.currentData(), "position": pos if pos == "auto" else int(pos)}

    def show_song(self, song_id: str):
        self.song_id = song_id
        self.song = self.win.lib.load_song(song_id)
        self.render()

    def render(self):
        if not self.song:
            return
        st = self.song.get("review", {}).get("status", "draft")
        flagged = sum(1 for b in self.song.get("bars", []) if b.get("doubts"))
        if not self.song.get("bars"):
            self.banner.setText("No music was found for this song. Check the page range on the Review tab.")
            self.banner.show()
            self.doc.close()
            return
        if st != "reviewed":
            self.banner.setText(
                f"This song was read automatically and hasn't been checked yet"
                f"{f' ({flagged} bars are flagged)' if flagged else ''}. "
                f"Wrong notes or chords are likely. <a href='review'>Check it on the Review tab</a>.")
            self.banner.show()
        else:
            self.banner.hide()
        self.status.setText("Engraving… (the very first time can take a minute while fonts are prepared)"
                            if not self.win.settings.get("engraved_once") else "Engraving…")
        self._request = (dict(self.song), self.options(), self.song_id)
        if self.job is not None and self.job.isRunning():
            return  # the running job will pick up the newest request when it finishes
        self._start_job()

    def busy(self) -> bool:
        return (self.job is not None and self.job.isRunning()) or self._request is not None

    def _start_job(self):
        song, opts, sid = self._request
        self._request = None
        job = RenderJob(song, opts, self.win.lib.root / "cache")
        job.done.connect(lambda path, warnings, sid=sid, opts=opts: self._rendered(path, warnings, sid, opts))
        job.failed.connect(lambda msg, sid=sid: self._failed(msg, sid))
        job.finished.connect(self._job_finished)
        self.job = KEEPER.start(job)

    def _job_finished(self):
        if self._request is not None:
            self._start_job()

    def _failed(self, msg: str, sid):
        if sid == self.song_id and self._request is None:
            self.status.setText("Could not engrave this song: " + msg.splitlines()[0])

    def _rendered(self, path: str, warnings: list, sid=None, opts=None):
        if sid != self.song_id or opts != self.options() or self._request is not None:
            return  # an older request; a newer one is on its way
        if not self.win.settings.get("engraved_once"):
            self.win.settings["engraved_once"] = True
            save_settings(self.win.settings)
        self.pdf_path = path
        self.doc.load(path)
        self.status.setText("" if not warnings else
                            f"{len(warnings)} bar(s) don't add up to a full bar yet; fix them on the Review tab.")

    def export_pdf(self):
        if not self.pdf_path:
            return
        o = self.options()
        name = f"{self.song.get('title', 'song')} ({o['key'] if o['key'] != 'Original' else self.song.get('key')}, {STYLE_NAMES[o['style']]}).pdf"
        start = self.win.settings.get("last_export_dir", "")
        target, _ = QFileDialog.getSaveFileName(self, "Save PDF", str(Path(start) / name) if start else name,
                                                "PDF (*.pdf)")
        if target:
            shutil.copyfile(self.pdf_path, target)
            self.win.settings["last_export_dir"] = str(Path(target).parent)
            save_settings(self.win.settings)

    def print_pdf(self):
        if not self.pdf_path:
            return
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(printer, self)
        if dlg.exec() != QDialog.Accepted:
            return
        painter = QPainter(printer)
        rect = painter.viewport()
        for i in range(self.doc.pageCount()):
            if i:
                printer.newPage()
            size = self.doc.pagePointSize(i).toSize() * (printer.resolution() / 72.0)
            img = self.doc.render(i, size)
            target = img.size().scaled(rect.size(), Qt.KeepAspectRatio)
            painter.drawImage(0, 0, img.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        painter.end()


# ====================================================================== review screen

class SystemPanel(QFrame):
    """One line of music: the scan, what was read, and an editable table of its bars."""

    def __init__(self, review: "ReviewView", index: int, system: dict):
        super().__init__()
        self.setObjectName("system")
        self.review, self.index, self.system = review, index, system
        self.scan = QLabel()
        self.scan.setAlignment(Qt.AlignLeft)
        self.preview = QLabel("Engraving…")
        self.preview.setAlignment(Qt.AlignLeft)
        crop = review.win.lib.root / "crops" / system.get("crop", "") if system.get("crop") else None
        if crop and crop.exists():
            self.scan.setPixmap(QPixmap(str(crop)).scaledToWidth(760, Qt.SmoothTransformation))
        else:
            self.scan.setText("(no scan for this line)")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Bar", "Chords", "Melody", "Check"])
        self.table.verticalHeader().hide()
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 170)
        self.table.setColumnWidth(2, 220)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.itemChanged.connect(self._edited)
        self.ok_btn = QPushButton("This line is right")
        self.ok_btn.setToolTip("Clears the flags on this line after you've compared it with the scan")
        self.ok_btn.clicked.connect(self._confirm)
        head = QHBoxLayout()
        title = QLabel(f"<b>Line {index + 1}</b>  ·  bars {system['first_bar'] + 1}–{system['last_bar'] + 1}"
                       + (f"  ·  page {system['page']}" if system.get("page") else ""))
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.ok_btn)
        cap1, cap2 = QLabel("Scan"), QLabel("What was read (flagged bars in orange)")
        for c in (cap1, cap2):
            c.setProperty("class", "caption")
        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(cap1)
        lay.addWidget(self.scan)
        lay.addWidget(cap2)
        lay.addWidget(self.preview)
        lay.addWidget(self.table)
        self.fill()

    def fill(self):
        song = self.review.song
        self.table.blockSignals(True)
        bars = range(self.system["first_bar"], self.system["last_bar"] + 1)
        self.table.setRowCount(len(bars))
        for r, bi in enumerate(bars):
            b = song["bars"][bi]
            num = QTableWidgetItem(str(bi + 1))
            num.setFlags(Qt.ItemIsEnabled)
            check = QTableWidgetItem("; ".join(b.get("doubts", [])) or "✓")
            check.setFlags(Qt.ItemIsEnabled)
            if b.get("doubts"):
                check.setForeground(Qt.darkYellow)
            chords = QTableWidgetItem(editing.chords_to_text(b.get("chords", [])))
            chords.setToolTip(editing.CHORD_HELP)
            mel = QTableWidgetItem(" ".join(b.get("notes", [])))
            mel.setToolTip(editing.MELODY_HELP)
            for c, it in enumerate((num, chords, mel, check)):
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)
        self.table.setFixedHeight(self.table.horizontalHeader().height() + 4 +
                                  sum(self.table.rowHeight(r) for r in range(self.table.rowCount())))

    def _edited(self, item: QTableWidgetItem):
        if item.column() not in (1, 2):
            return
        song = self.review.song
        bi = self.system["first_bar"] + item.row()
        bar = song["bars"][bi]
        bpb = editing.beats_per_bar(song)
        try:
            if item.column() == 1:
                bar["chords"] = editing.text_to_chords(item.text(), int(bpb))
            else:
                toks, _ = editing.check_melody(item.text())
                bar["notes"] = toks
        except ValueError as e:
            QMessageBox.warning(self, "Can't use that", str(e))
            self.fill()
            return
        editing.recheck_bar(bar, bpb, bi == len(song["bars"]) - 1)
        self.review.changed()
        self.fill()

    def _confirm(self):
        song = self.review.song
        bpb = editing.beats_per_bar(song)
        for bi in range(self.system["first_bar"], self.system["last_bar"] + 1):
            editing.recheck_bar(song["bars"][bi], bpb, bi == len(song["bars"]) - 1)
        self.review.changed()
        self.fill()

    def set_preview(self, path: str):
        if path and Path(path).exists():
            pm = QPixmap(path)
            self.preview.setPixmap(pm.scaledToWidth(min(760, pm.width()), Qt.SmoothTransformation))
        else:
            self.preview.setText("(could not draw this line)")


class ReviewView(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.song_id = None
        self.song = None
        self.panels: list[SystemPanel] = []
        self.title = QLineEdit()
        self.composer = QLineEdit()
        self.style_txt = QLineEdit()
        self.style_txt.setPlaceholderText("e.g. Med. Swing")
        self.key = _combo([k for k in KEYS if k != "Original"])
        for w in (self.title, self.composer, self.style_txt):
            w.editingFinished.connect(self._meta_changed)
        self.key.currentIndexChanged.connect(self._meta_changed)
        self.status = QLabel()
        self.done_btn = QPushButton("Mark song as checked")
        self.done_btn.clicked.connect(self._toggle_reviewed)
        form = QFormLayout()
        form.addRow("Title", self.title)
        row = QHBoxLayout()
        row.addWidget(QLabel("Composer"))
        row.addWidget(self.composer, 2)
        row.addWidget(QLabel("Style"))
        row.addWidget(self.style_txt, 1)
        row.addWidget(QLabel("Key"))
        row.addWidget(self.key)
        top = QVBoxLayout()
        top.addLayout(form)
        top.addLayout(row)
        st = QHBoxLayout()
        st.addWidget(self.status, 1)
        st.addWidget(self.done_btn)
        top.addLayout(st)
        help_lbl = QLabel("Compare each line of the scan with what was read below it. Double-click a Chords or Melody "
                          "cell to correct it (hover over a cell for the format). The Check column says what looks wrong.")
        help_lbl.setWordWrap(True)
        help_lbl.setProperty("class", "caption")
        top.addWidget(help_lbl)
        self.inner = QWidget()
        self.inner_lay = QVBoxLayout(self.inner)
        self.inner_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.inner)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(scroll, 1)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save_and_refresh)
        self.prev_job = None
        self._prev_request = None
        self._prev_gen = 0

    def show_song(self, song_id: str):
        if self._save_timer.isActive():  # save pending edits of the previous song first
            self._save_timer.stop()
            self.win.lib.save_song(self.song_id, self.song)
        self.song_id = song_id
        self.song = self.win.lib.load_song(song_id)
        for w, v in ((self.title, self.song.get("title", "")), (self.composer, self.song.get("composer", "")),
                     (self.style_txt, self.song.get("style", ""))):
            w.blockSignals(True)
            w.setText(v)
            w.blockSignals(False)
        self.key.blockSignals(True)
        self.key.setCurrentIndex(max(0, self.key.findData(self.song.get("key", "C"))))
        self.key.blockSignals(False)
        for p in self.panels:
            p.setParent(None)
        self.panels = []
        systems = self.song.get("systems") or [
            {"first_bar": i, "last_bar": min(i + 3, len(self.song.get("bars", [])) - 1)}
            for i in range(0, len(self.song.get("bars", [])), 4)]
        for i, sy in enumerate(systems):
            p = SystemPanel(self, i, sy)
            self.inner_lay.insertWidget(self.inner_lay.count() - 1, p)
            self.panels.append(p)
        self._update_status()
        self._previews()

    def _update_status(self):
        flagged = sum(1 for b in self.song.get("bars", []) if b.get("doubts"))
        reviewed = self.song.get("review", {}).get("status") == "reviewed"
        note = self.song.get("review", {}).get("note", "")
        self.status.setText(("Checked." if reviewed else "Not checked yet.") +
                            (f"  {flagged} bar(s) flagged." if flagged else "  No bars flagged.") +
                            (f"\n{note}" if note else ""))
        self.status.setWordWrap(True)
        self.done_btn.setText("Mark as not checked" if reviewed else "Mark song as checked")

    def _previews(self):
        if not self.song.get("bars"):
            return
        data = dict(self.song)
        data["systems"] = [p.system for p in self.panels]
        self._prev_gen += 1
        self._prev_request = (data, self.song_id, self._prev_gen)
        if self.prev_job is not None and self.prev_job.isRunning():
            return
        self._start_previews()

    def busy(self) -> bool:
        return (self._save_timer.isActive() or (self.prev_job is not None and self.prev_job.isRunning())
                or self._prev_request is not None)

    def _start_previews(self):
        data, sid, gen = self._prev_request
        self._prev_request = None
        job = PreviewJob(data, self.win.lib.root / "cache" / "preview" / sid / str(gen))
        job.done.connect(lambda paths, sid=sid, gen=gen: self._previews_done(paths, sid, gen))
        job.failed.connect(lambda m, sid=sid, gen=gen: self._previews_done([], sid, gen))
        job.finished.connect(lambda: self._start_previews() if self._prev_request is not None else None)
        self.prev_job = KEEPER.start(job)

    def _previews_done(self, paths: list, sid: str, gen: int):
        if sid != self.song_id or gen != self._prev_gen:
            return  # the song changed or a newer edit is being drawn
        for i, p in enumerate(self.panels):
            p.set_preview(paths[i] if i < len(paths) else "")
        # tidy older preview images of this song
        root = self.win.lib.root / "cache" / "preview" / sid
        for d in root.glob("*"):
            if d.is_dir() and d.name != str(gen):
                shutil.rmtree(d, ignore_errors=True)

    def changed(self):
        self._update_status()
        self._save_timer.start()

    def _save_and_refresh(self):
        self.win.lib.save_song(self.song_id, self.song)
        self.win.song_saved(self.song_id)
        self._previews()

    def _meta_changed(self):
        if not self.song:
            return
        self.song["title"] = self.title.text().strip() or self.song.get("title", "")
        self.song["composer"] = self.composer.text().strip()
        self.song["style"] = self.style_txt.text().strip()
        self.song["key"] = self.key.currentData()
        self.changed()

    def _toggle_reviewed(self):
        rv = self.song.setdefault("review", {})
        if rv.get("status") == "reviewed":
            rv["status"] = "draft"
        else:
            flagged = sum(1 for b in self.song.get("bars", []) if b.get("doubts"))
            if flagged and QMessageBox.question(
                    self, "Bars still flagged",
                    f"{flagged} bar(s) are still flagged. Mark the song as checked anyway?") != QMessageBox.Yes:
                return
            rv["status"] = "reviewed"
        self.changed()


# ====================================================================== import dialog

class ImportDialog(QDialog):
    def __init__(self, parent, pdf_path: str):
        super().__init__(parent)
        import pymupdf

        self.setWindowTitle("Import a Real Book")
        self.resize(720, 620)
        self.pdf_path = pdf_path
        with pymupdf.open(pdf_path) as d:
            self.page_count = d.page_count
        self.book_title = QLineEdit(Path(pdf_path).stem)
        self.method = QComboBox()
        self.bookmarks = plan_from_bookmarks(pdf_path)
        if self.bookmarks:
            self.method.addItem(f"Use the PDF's bookmarks ({len(self.bookmarks)} songs)", "bookmarks")
        self.method.addItem("One song per page (name them later)", "pages")
        self.method.addItem("Use an index file (title and page number per line)", "index")
        self.index_btn = QPushButton("Choose index file…")
        self.index_path = None
        self.offset = QSpinBox()
        self.offset.setRange(-50, 50)
        self.offset.setToolTip("PDF page number minus the page number printed in the book")
        self.first = QSpinBox()
        self.first.setRange(1, self.page_count)
        self.last = QSpinBox()
        self.last.setRange(1, self.page_count)
        self.last.setValue(self.page_count)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Title", "From page", "To page"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.summary = QLabel()
        self.summary.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Book name", self.book_title)
        form.addRow("Find songs by", self.method)
        idx = QHBoxLayout()
        idx.addWidget(self.index_btn)
        self.offset_label = QLabel("Page offset")
        idx.addWidget(self.offset_label)
        idx.addWidget(self.offset)
        idx.addStretch(1)
        form.addRow("", idx)
        rng = QHBoxLayout()
        rng.addWidget(QLabel("from PDF page"))
        rng.addWidget(self.first)
        rng.addWidget(QLabel("to"))
        rng.addWidget(self.last)
        rng.addStretch(1)
        form.addRow("Import", rng)
        self.reread = QCheckBox("Read songs again that are already in the library (replaces them; songs you've "
                                "marked as checked are kept)")
        form.addRow("", self.reread)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Start import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Songs to import (you can edit titles and pages):"))
        lay.addWidget(self.table, 1)
        lay.addWidget(self.summary)
        lay.addWidget(buttons)
        for w in (self.method, self.offset, self.first, self.last):
            (w.currentIndexChanged if isinstance(w, QComboBox) else w.valueChanged).connect(self.refresh)
        self.index_btn.clicked.connect(self.choose_index)
        self.refresh()

    def choose_index(self):
        p, _ = QFileDialog.getOpenFileName(self, "Index file", "", "Index (*.csv *.txt);;All files (*)")
        if p:
            self.index_path = p
            self.refresh()

    def plan(self) -> list[PlannedSong]:
        m = self.method.currentData()
        if m == "bookmarks":
            pl = self.bookmarks
        elif m == "index":
            pl = plan_from_index(self.index_path, self.offset.value(), self.page_count) if self.index_path else []
        else:
            pl = plan_one_per_page(self.pdf_path, self.first.value(), self.last.value())
        return [p for p in pl if self.first.value() <= p.first_page <= self.last.value()]

    def refresh(self):
        is_index = self.method.currentData() == "index"
        self.index_btn.setVisible(is_index)
        self.offset.setVisible(is_index)
        self.offset_label.setVisible(is_index)
        pl = self.plan()
        self.table.setRowCount(len(pl))
        for r, p in enumerate(pl):
            for c, v in enumerate((p.title, str(p.first_page), str(p.last_page))):
                self.table.setItem(r, c, QTableWidgetItem(v))
        pages = sum(p.last_page - p.first_page + 1 for p in pl)
        self.summary.setText(
            f"{len(pl)} songs, {pages} pages. Reading takes about 15 seconds a page on a typical PC "
            f"(roughly {max(1, round(pages * 15 / 60))} minutes); you can keep using the app meanwhile, and "
            f"an interrupted import continues where it stopped." if pl else
            "No songs to import yet. Choose how to find them above.")

    def edited_plan(self) -> list[PlannedSong]:
        out = []
        for r in range(self.table.rowCount()):
            try:
                t = self.table.item(r, 0).text().strip()
                a, b = int(self.table.item(r, 1).text()), int(self.table.item(r, 2).text())
            except (AttributeError, ValueError):
                continue
            if t and 1 <= a <= b <= self.page_count:
                out.append(PlannedSong(t, a, b))
        return out


# ====================================================================== settings

class SettingsDialog(QDialog):
    def __init__(self, parent, settings: dict):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.s = dict(settings)
        self.lib = QLineEdit(self.s["library"])
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse)
        self.style = _combo(STYLE_NAMES, self.s["style"])
        self.tuning = _combo(TUNING_NAMES, self.s["tuning"])
        self.open_strings = _combo({"avoid": "Avoid", "allow": "Allow"}, self.s["open_strings"])
        form = QFormLayout(self)
        row = QHBoxLayout()
        row.addWidget(self.lib, 1)
        row.addWidget(browse)
        form.addRow("Library folder", row)
        form.addRow("Default voicings", self.style)
        form.addRow("Default tuning", self.tuning)
        form.addRow("Open strings in tab", self.open_strings)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _ok(self):
        problem = check_writable(Path(self.lib.text()))
        if problem:
            QMessageBox.warning(self, "Can't use that folder", problem)
            return
        self.accept()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Library folder", self.lib.text() or "")
        if d:
            self.lib.setText(d)

    def values(self) -> dict:
        self.s.update(library=self.lib.text(), style=self.style.currentData(), tuning=self.tuning.currentData(),
                      open_strings=self.open_strings.currentData())
        return self.s


# ====================================================================== main window

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.lib = Library(self.settings["library"])
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 860)
        self.import_thread = None

        # library panel
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search songs")
        self.search.textChanged.connect(self.refresh_list)
        self.filter = _combo({"all": "All songs", "draft": "Not checked yet", "reviewed": "Checked"})
        self.filter.currentIndexChanged.connect(self.refresh_list)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selected)
        self.count = QLabel()
        self.count.setProperty("class", "caption")
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(self.search)
        ll.addWidget(self.filter)
        ll.addWidget(self.list, 1)
        ll.addWidget(self.count)

        # right side
        self.song_view = SongView(self)
        self.review_view = ReviewView(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.song_view, "Lead sheet")
        self.tabs.addTab(self.review_view, "Review")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.empty = QLabel("<h2>Your library is empty</h2><p>Use <b>File → Import a Real Book PDF…</b> to add songs.</p>")
        self.empty.setAlignment(Qt.AlignCenter)
        self.right = QStackedWidget()
        self.right.addWidget(self.empty)
        self.right.addWidget(self.tabs)

        # import panel: shown above everything while a book is being read
        self.imp_panel = QFrame()
        self.imp_panel.setObjectName("importPanel")
        self.imp_title = QLabel()
        self.imp_title.setObjectName("importTitle")
        self.imp_detail = QLabel()
        self.imp_detail.setWordWrap(True)
        self.prog = QProgressBar()
        self.prog.setTextVisible(True)
        self.cancel_btn = QPushButton("Stop import")
        self.cancel_btn.clicked.connect(self._cancel_import)
        pl = QVBoxLayout(self.imp_panel)
        top = QHBoxLayout()
        top.addWidget(self.imp_title, 1)
        top.addWidget(self.cancel_btn)
        pl.addLayout(top)
        pl.addWidget(self.prog)
        pl.addWidget(self.imp_detail)
        self.imp_panel.hide()
        self._imp_song = ("", 0, 0)
        self._imp_eta = -1.0
        # keep the old names working for code that refers to them
        self.prog_label = self.imp_detail
        right_box = QWidget()
        rb = QVBoxLayout(right_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.addWidget(self.imp_panel)
        rb.addWidget(self.right, 1)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right_box)
        split.setSizes([280, 1000])
        self.setCentralWidget(split)

        m = self.menuBar().addMenu("&File")
        a = QAction("Import a Real Book PDF…", self)
        a.triggered.connect(self.import_pdf)
        m.addAction(a)
        a = QAction("Open library folder", self)
        a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.lib.root))))
        m.addAction(a)
        a = QAction("Settings…", self)
        a.triggered.connect(self.open_settings)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Quit", self)
        a.triggered.connect(self.close)
        m.addAction(a)
        s = self.menuBar().addMenu("&Song")
        a = QAction("Delete this song", self)
        a.triggered.connect(self.delete_song)
        s.addAction(a)
        h = self.menuBar().addMenu("&Help")
        a = QAction("Show import log", self)
        a.triggered.connect(self.show_log)
        h.addAction(a)
        a = QAction("Show app log", self)
        a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(settings_dir() / "app.log"))))
        h.addAction(a)
        a = QAction("About", self)
        a.triggered.connect(self.about)
        h.addAction(a)
        self.refresh_list()

    # ---- library list
    def refresh_list(self, select: str | None = None):
        cur = select or (self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None)
        q = self.search.text().strip().lower()
        f = self.filter.currentData()
        entries = self.lib.entries()
        self.list.blockSignals(True)
        self.list.clear()
        shown = 0
        for e in entries:
            if q and q not in e.title.lower():
                continue
            if f != "all" and e.status != f:
                continue
            label = e.title + ("" if e.status == "reviewed" else f"   · to check{f' ({e.flags})' if e.flags else ''}")
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, e.id)
            if e.status == "reviewed":
                font = it.font()
                font.setBold(True)
                it.setFont(font)
            self.list.addItem(it)
            shown += 1
            if e.id == cur:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        reviewed = sum(1 for e in entries if e.status == "reviewed")
        self.count.setText(f"{len(entries)} songs · {reviewed} checked")
        if not entries:
            running = self.import_thread is not None and self.import_thread.isRunning()
            self.empty.setText(
                "<h2>Reading your book…</h2><p>Songs appear in the list as soon as each one is read.<br>"
                "The first can take a minute; progress is shown above.</p>" if running else
                "<h2>Your library is empty</h2><p>Use <b>File → Import a Real Book PDF…</b> to add songs.</p>")
        self.right.setCurrentIndex(1 if entries else 0)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        elif self.list.currentItem() is not None and cur and self.song_view.song_id != cur:
            self._selected(self.list.currentItem(), None)

    def _selected(self, item, _prev):
        if not item:
            return
        sid = item.data(Qt.UserRole)
        self.song_view.show_song(sid)
        self.review_view.show_song(sid)
        if self.song_view.song and self.song_view.song.get("review", {}).get("status") != "reviewed":
            self.tabs.setCurrentIndex(1)

    def song_saved(self, song_id: str):
        if self.song_view.song_id == song_id:
            self.song_view.song = self.lib.load_song(song_id)
            if self.tabs.currentIndex() == 0:
                self.song_view.render()
        self.refresh_list(select=song_id)

    def _tab_changed(self, i):
        if i == 0 and self.song_view.song_id:
            self.song_view.song = self.lib.load_song(self.song_view.song_id)
            self.song_view.render()

    def delete_song(self):
        it = self.list.currentItem()
        if not it:
            return
        if QMessageBox.question(self, "Delete song", f"Delete “{it.text().split('   ·')[0]}” from the library? "
                                "The PDF itself is not touched.") == QMessageBox.Yes:
            self.lib.delete_song(it.data(Qt.UserRole))
            self.refresh_list()

    # ---- import
    def import_pdf(self):
        if self.import_thread and self.import_thread.isRunning():
            QMessageBox.information(self, "Import running", "Wait for the current import to finish, or stop it first.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose a Real Book PDF", self.settings.get("last_pdf_dir", ""),
                                              "PDF (*.pdf)")
        if not path:
            return
        self.settings["last_pdf_dir"] = str(Path(path).parent)
        save_settings(self.settings)
        dlg = ImportDialog(self, path)
        if dlg.exec() != QDialog.Accepted:
            return
        plan = dlg.edited_plan()
        if not plan:
            QMessageBox.warning(self, "Nothing to import",
                                "The song list was empty, so nothing was imported. Open the PDF again and choose "
                                "“One song per page”, or load an index file.")
            return
        self.start_import(path, dlg.book_title.text().strip() or Path(path).stem, plan, dlg.reread.isChecked())

    def start_import(self, path: str, title: str, plan: list, reread: bool = False):
        log.info("import requested: %s, %d songs, library %s", path, len(plan), self.lib.root)
        self.import_book_title = title
        job = ImportJob(self.lib, path, title, plan, reread)
        self.import_thread = ImportThread(job)
        self.import_thread.progress.connect(self._import_progress)
        self.import_thread.line.connect(self._import_line)
        self.import_thread.song_added.connect(lambda sid: self.refresh_list())
        self.import_thread.summary.connect(self._import_summary)
        self.import_thread.failed.connect(self._import_failed)
        self.import_thread.finished.connect(self._import_finished)
        self.imp_title.setText(f"Importing “{title}”")
        self.imp_detail.setText("Starting…")
        self.prog.setRange(0, 0)  # busy until the first song starts
        self.cancel_btn.setEnabled(True)
        self.imp_panel.show()
        self.import_thread.start()
        self.refresh_list()

    def _import_failed(self, msg: str):
        log.error("import failed: %s", msg)
        QMessageBox.warning(self, "Import stopped", "The import stopped because of an error:\n\n" + msg.splitlines()[0]
                            + "\n\nDetails are in the app log (Help → Show app log).")

    @staticmethod
    def _fmt_time(sec: float) -> str:
        if sec < 0:
            return ""
        m = int(round(sec / 60))
        if m < 1:
            return "less than a minute left"
        return f"about {m} min left" if m < 60 else f"about {m // 60} h {m % 60:02d} min left"

    def _import_progress(self, done: int, total: int, title: str, eta: float):
        if not title:
            return
        self.prog.setRange(0, max(1, total))
        self.prog.setValue(done)
        self.prog.setFormat(f"song {done + 1} of {total}")
        self._imp_song = (title, done + 1, total)
        self._imp_eta = eta
        self.imp_detail.setText(f"Reading “{title}”…  {self._fmt_time(eta)}")
        self.setWindowTitle(f"{APP_NAME} — importing {done + 1}/{total}")

    def _import_line(self, page: int, line: int, lines: int):
        title, k, n = self._imp_song
        eta = self._fmt_time(self._imp_eta)
        self.imp_detail.setText(f"Reading “{title}”: page {page}, line {line} of {lines}" + (f"  ·  {eta}" if eta else ""))

    def _import_summary(self, s: dict):
        self._last_summary = s

    def _import_finished(self):
        self.setWindowTitle(APP_NAME)
        s = getattr(self, "_last_summary", None)
        cancelled = self.import_thread.job.cancelled
        self.prog.setRange(0, 1)
        self.prog.setValue(1)
        self.prog.setFormat("")
        self.cancel_btn.setEnabled(False)
        parts = []
        if s:
            parts.append(f"{s['imported']} songs read")
            if s["no_music"]:
                parts.append(f"{s['no_music']} pages without music skipped (cover, contents…)")
            if s["failed"]:
                parts.append(f"{s['failed']} could not be read: {', '.join(s['failed_titles'][:5])}"
                             + ("…" if s["failed"] > 5 else "") + " (details in import.log in the library folder)")
        self.imp_title.setText("Import stopped" if cancelled else "Import finished")
        self.imp_detail.setText(" · ".join(parts) + ("  —  import the same PDF again to continue where it stopped"
                                                     if cancelled else ""))
        QTimer.singleShot(60000, self.imp_panel.hide)
        self.refresh_list()

    def _cancel_import(self):
        if self.import_thread:
            self.import_thread.cancel()
            self.cancel_btn.setEnabled(False)
            self.imp_detail.setText("Stopping after the current line…")

    # ---- settings / about
    def open_settings(self):
        dlg = SettingsDialog(self, self.settings)
        if dlg.exec() == QDialog.Accepted:
            self.settings = dlg.values()
            save_settings(self.settings)
            self.lib = Library(self.settings["library"])
            self.refresh_list()

    def show_log(self):
        log = self.lib.root / "import.log"
        if not log.exists():
            QMessageBox.information(self, "Import log", "Nothing has been imported yet.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log)))

    def about(self):
        QMessageBox.about(self, APP_NAME, (
            f"<b>{APP_NAME}</b><br>Reads Real Book pages and prints lead sheets with tab and jazz guitar voicings."
            "<br><br>Music recognition: JAZZMUS (University of Alicante and JKU Linz, MIT license). "
            "Engraving: GNU LilyPond."))

    def closeEvent(self, ev):
        if self.import_thread and self.import_thread.isRunning():
            self.import_thread.cancel()
            self.import_thread.wait(30000)
        KEEPER.wait_all(30000)
        super().closeEvent(ev)


log = logging.getLogger("realbook")


def _setup_logging():
    """Everything that goes wrong ends up in app.log (Help → Show app log), including hard crashes."""
    path = settings_dir() / "app.log"
    try:
        if path.exists() and path.stat().st_size > 2_000_000:
            path.replace(path.with_suffix(".old.log"))
    except OSError:
        pass
    logging.basicConfig(filename=str(path), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    global _crash_file
    _crash_file = open(path, "a", encoding="utf-8")  # kept open for faulthandler
    faulthandler.enable(_crash_file)

    def hook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        log.error("unhandled error:\n%s", text)
        app = QApplication.instance()
        if app:
            QMessageBox.critical(None, APP_NAME, "Something went wrong:\n\n" + str(value)
                                 + "\n\nThe details are in the app log (Help → Show app log).")

    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)

    def qt_messages(mode, ctx, msg):
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            log.warning("Qt: %s", msg)

    qInstallMessageHandler(qt_messages)
    log.info("started %s (python %s, %s)", APP_NAME, sys.version.split()[0], sys.platform)


def choose_library(parent, settings: dict) -> bool:
    """Ask where the song library should live. Returns False if the person cancels."""
    QMessageBox.information(
        parent, APP_NAME,
        "Choose a folder for your song library.\n\nThe app keeps the songs it reads, the scanned lines and your "
        "corrections there. Pick an empty folder, or create one in the next window. Your Real Book PDFs are not "
        "moved or changed.")
    while True:
        d = QFileDialog.getExistingDirectory(parent, "Folder for your song library", settings.get("library") or "")
        if not d:
            return False
        problem = check_writable(Path(d))
        if problem:
            QMessageBox.warning(parent, "Can't use that folder", problem)
            continue
        settings["library"] = d
        save_settings(settings)
        log.info("library folder set to %s", d)
        return True


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(STYLE_SHEET)
    _setup_logging()
    settings = load_settings()
    if not settings.get("library") or check_writable(Path(settings["library"])):
        if settings.get("library"):
            QMessageBox.warning(None, APP_NAME, check_writable(Path(settings["library"])))
        if not choose_library(None, settings):
            return 0
    w = MainWindow()
    w.show()
    code = app.exec()
    log.info("closed")
    return code


if __name__ == "__main__":
    sys.exit(main())
