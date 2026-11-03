"""The song library on disk.

<root>/
  songs/<song_id>.json        one song (melody, chords, review flags, where it came from)
  crops/<song_id>_<n>.png     the scanned image of each system, for the review screen
  books/<book_id>.json        an imported PDF: its path and the list of songs planned from it
  settings.json               app settings
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SETTINGS = {
    "library": None,  # chosen by the user on first start; never picked silently
    "style": "drop3",
    "tuning": "standard",
    "open_strings": "avoid",
    "position": "auto",
    "last_pdf_dir": "",
    "last_export_dir": "",
}


def settings_dir() -> Path:
    override = os.environ.get("RBG_SETTINGS_DIR")
    base = Path(override) if override else Path(os.environ.get("APPDATA", Path.home() / ".config")) / "RealBookGuitar"
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_file() -> Path:
    return settings_dir() / "settings.json"


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(settings_file().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if os.environ.get("RBG_LIBRARY"):
        s["library"] = os.environ["RBG_LIBRARY"]
    return s


def save_settings(s: dict) -> None:
    settings_file().write_text(json.dumps(s, indent=1), encoding="utf-8")


def check_writable(folder: Path) -> str | None:
    """Return None if the app can create and write files in folder, else a plain explanation."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return None
    except OSError as e:
        hint = ""
        if os.name == "nt" and "Documents" in str(folder):
            hint = (" Windows Security's “Controlled folder access” blocks unknown apps from writing in "
                    "Documents; either choose another folder or allow Real Book Guitar in "
                    "Windows Security → Ransomware protection → Allow an app.")
        return f"Can't write in {folder} ({e.strerror or e}).{hint}"


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:40] or "book"


@dataclass
class SongEntry:
    id: str
    title: str
    book: str
    pages: list[int]
    status: str  # "draft" or "reviewed"
    flags: int  # bars with review flags


class Library:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        for sub in ("songs", "crops", "books"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ---- songs
    def song_path(self, song_id: str) -> Path:
        return self.root / "songs" / f"{song_id}.json"

    def load_song(self, song_id: str) -> dict:
        return json.loads(self.song_path(song_id).read_text(encoding="utf-8"))

    def save_song(self, song_id: str, data: dict) -> None:
        tmp = self.song_path(song_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.song_path(song_id))  # atomic: a crash never leaves a half-written song

    def has_song(self, song_id: str) -> bool:
        return self.song_path(song_id).exists()

    def entries(self) -> list[SongEntry]:
        out = []
        for f in sorted((self.root / "songs").glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append(SongEntry(f.stem, d.get("title", f.stem), d.get("book", ""), d.get("pages", []),
                                 d.get("review", {}).get("status", "draft"),
                                 sum(1 for b in d.get("bars", []) if b.get("doubts"))))
        out.sort(key=lambda e: e.title.lower())
        return out

    def delete_song(self, song_id: str) -> None:
        self.song_path(song_id).unlink(missing_ok=True)
        for c in (self.root / "crops").glob(f"{song_id}_*.png"):
            c.unlink(missing_ok=True)

    # ---- books
    def book_path(self, book_id: str) -> Path:
        return self.root / "books" / f"{book_id}.json"

    def save_book(self, book_id: str, data: dict) -> None:
        self.book_path(book_id).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    def load_book(self, book_id: str) -> dict | None:
        try:
            return json.loads(self.book_path(book_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def books(self) -> dict[str, dict]:
        out = {}
        for f in (self.root / "books").glob("*.json"):
            try:
                out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return out

    def crop_path(self, song_id: str, n: int) -> Path:
        return self.root / "crops" / f"{song_id}_{n:02d}.png"
