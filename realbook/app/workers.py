"""Background jobs so the window never freezes: rendering, previews, importing."""
from __future__ import annotations

import hashlib
import json
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from ..render_ly import arrange, compile_pdf, to_lilypond
from ..song import from_dict


class _Thread(QThread):
    failed = Signal(str)

    def run(self):
        try:
            self.work()
        except Exception as e:  # report, don't crash the app
            self.failed.emit(f"{e}\n\n{traceback.format_exc(limit=3)}")

    def work(self):
        raise NotImplementedError


class RenderJob(_Thread):
    """Song + options -> PDF lead sheet (cached by content and options)."""

    done = Signal(str, list)  # pdf path, warnings

    def __init__(self, song_data: dict, opts: dict, cache_dir: Path):
        super().__init__()
        self.song_data, self.opts, self.cache_dir = song_data, dict(opts), cache_dir

    def work(self):
        key = hashlib.sha1(json.dumps([self.song_data.get("bars"), self.song_data.get("key"),
                                       self.song_data.get("title"), self.song_data.get("style"),
                                       self.song_data.get("composer"), self.song_data.get("line_breaks"),
                                       self.opts], sort_keys=True).encode()).hexdigest()[:16]
        out = self.cache_dir / f"{key}.pdf"
        if out.exists():
            self.done.emit(str(out), [])
            return
        song = from_dict(self.song_data)
        if self.opts.get("key") and self.opts["key"] != "Original":
            song = song.transposed(self.opts["key"])
        arr = arrange(song, self.opts["style"], self.opts["tuning"], "guitar", self.opts["open_strings"],
                      self.opts["position"])
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        warnings = compile_pdf(to_lilypond(arr), out, strict=False)
        self.done.emit(str(out), warnings)


class PreviewJob(_Thread):
    done = Signal(list)

    def __init__(self, song_data: dict, out_dir: Path):
        super().__init__()
        self.song_data, self.out_dir = song_data, out_dir

    def work(self):
        from ..preview import system_previews

        self.done.emit([str(p) if p else "" for p in system_previews(self.song_data, self.out_dir)])


class ImportThread(_Thread):
    progress = Signal(int, int, str, float)
    line = Signal(int, int, int)  # page, line number, lines on page
    song_added = Signal(str)
    summary = Signal(dict)

    def __init__(self, job):
        super().__init__()
        self.job = job

    def work(self):
        result = self.job.run(
            on_progress=lambda d, t, title, eta: self.progress.emit(d, t, title, -1.0 if eta is None else eta),
            on_song=self.song_added.emit,
            on_line=lambda page, li, n: self.line.emit(page, li, n))
        self.summary.emit(result)

    def cancel(self):
        self.job.cancelled = True


class JobKeeper:
    """Holds every background job until it has really finished.

    Dropping the last reference to a running QThread destroys it mid-run and Qt aborts the whole
    program, which is what crashed the review screen. Jobs live here until 'finished' fires.
    """

    def __init__(self):
        self.jobs: set = set()

    def start(self, job: QThread) -> QThread:
        self.jobs.add(job)
        job.finished.connect(lambda j=job: self._done(j))
        job.start()
        return job

    def _done(self, job):
        # release our hold only; Python frees the object once nothing else refers to it.
        # (deleteLater would destroy it while a view still holds it, which also crashes.)
        self.jobs.discard(job)

    def busy(self) -> bool:
        return any(j.isRunning() for j in self.jobs)

    def wait_all(self, ms: int = 30000):
        for j in list(self.jobs):
            j.wait(ms)


KEEPER = JobKeeper()
