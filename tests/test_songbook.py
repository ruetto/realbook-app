"""Songbook and PDF export: page numbers in the index point at the right tunes, nothing is lost."""
import json
from pathlib import Path

import pymupdf
import pytest

from realbook.songbook import (BookOptions, build_songbook, collect_glossary, export_pdfs, index_rows,
                               safe_filename, sort_title, surname_key)
from realbook.render_ly import arrange
from realbook.song import from_dict

FIX = Path(__file__).parent / "fixtures"


def _songs():
    a = json.loads((FIX / "test_tune.json").read_text(encoding="utf-8"))
    m = json.loads((FIX / "minor_test.json").read_text(encoding="utf-8"))
    b = dict(a, title="'Bossa Test", composer="Antonio Carlos Testim")
    return [("t1", a), ("t2", m), ("t3", b)]


def test_titles_composers_and_filenames():
    assert sort_title("'Round Midnight") == "round midnight"
    assert sort_title("(I'm Afraid) The Masquerade") == "i'm afraid) the masquerade"
    assert surname_key("Antonio Carlos Jobim") < surname_key("Thelonious Monk")
    assert surname_key("John Lennon, Paul McCartney").startswith("lennon")
    assert safe_filename('What Is This Thing Called Love?') == "What Is This Thing Called Love-"
    assert safe_filename("AC/DC: <live>") == "AC-DC- -live-"


def test_index_groups_by_letter():
    rows = index_rows([{"title": "'Round Midnight", "composer": "Monk", "key": "E♭m", "page": 9},
                       {"title": "Ruby", "composer": "", "key": "C", "page": 3}], "title")
    assert rows[0] == ("head", "R") and [r[1] for r in rows[1:]] == ["'Round Midnight", "Ruby"]


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("book")
    res = build_songbook(_songs(), BookOptions(owner="Test", workers=2), tmp / "book.pdf", tmp / "cache")
    return res, tmp


def test_songbook_index_points_at_each_tune(book):
    res, _ = book
    assert res.songs == 3 and not res.skipped
    d = pymupdf.open(res.path)
    assert d.page_count == res.pages
    toc = {t[1]: t[2] for t in d.get_toc() if t[0] == 2}
    for song_id, data in _songs():
        page = d[toc[data["title"]] - 1]
        assert data["title"].replace("'", "’")[:8] in page.get_text().replace("'", "’"), data["title"]
    # the index page lists every tune with that same page number, and links to it
    idx = d[1].get_text()
    for title, pg in toc.items():
        assert title in idx and str(pg) in idx
    assert {lk["page"] for lk in d[1].get_links() if lk["kind"] == pymupdf.LINK_GOTO} == {p - 1 for p in toc.values()}
    # tunes of two or more pages start on a left-hand (even) page
    starts = sorted(toc.values())
    gl = [t[2] for t in d.get_toc() if t[1] == "Chord glossary"][0]
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else gl
        body = [p for p in range(s, nxt) if "Notes" not in d[p - 1].get_text()[:40]]
        if len(body) >= 2:
            assert s % 2 == 0, (s, body)


def test_glossary_has_every_voicing(book):
    res, _ = book
    arrs = [arrange(from_dict(d), BookOptions().style, "standard") for _, d in _songs()]
    items = collect_glossary(arrs)
    assert res.glossary_voicings == len(items)
    used = {(ch.symbol.strip("()"), tuple(v.frets)) for a in arrs for _, ch, v in a.voicings}
    assert {(ch.symbol.strip("()"), tuple(v.frets)) for ch, v in items} == used


def test_export_one_pdf_per_tune(book, tmp_path):
    _, btmp = book
    files, skipped = export_pdfs(_songs(), BookOptions(workers=2), tmp_path / "out", btmp / "cache")
    assert not skipped and len(files) == 3
    for f in files:
        d = pymupdf.open(f)
        assert d.metadata["title"] and d.metadata["title"][:5] in f.name
        assert not any(lk.get("uri", "").startswith(("rbsong:", "textedit:")) for p in d for lk in p.get_links())
