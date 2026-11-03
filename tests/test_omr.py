"""Tests for page reading: staff finding, the ONNX recognizer, and conversion to song files."""
import subprocess
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from realbook.engraver import lilypond_exe
from realbook.omr.kern2song import harte_to_realbook, staves_to_song
from realbook.omr.staves import crop, find_staves
from realbook.song import from_dict, load

FIXTURE = Path(__file__).parent / "fixtures" / "test_tune.json"


@pytest.mark.parametrize("harte,rb", [
    ("F:maj7", "Fmaj7"), ("A:hdim7", "A-7b5"), ("D:7(b9)", "D7b9"), ("G:min7", "G-7"), ("C:7", "C7"),
    ("Bb:maj6", "Bb6"), ("Bb:min6", "Bb-6"), ("B-:min7", "Bb-7"), ("C:maj", "C"), ("D:dim7", "D°7"),
    ("E:7(#9,b13)", "E7#9b13"), ("G:min7/F", "G-7/F"), ("C:sus4", "Csus4"),
])
def test_chord_names_become_real_book_symbols(harte, rb):
    assert harte_to_realbook(harte) == rb


STAFF_1 = """**kern\t**mxhm
*clefG2\t*
*k[b-]\t*
*M4/4\t*
=\t=
4r\tF:maj7
4c\t.
4c\t.
4c\t.
=\t=
2.e-\tA:hdim7
.\tD:7(b9)
4e\t.
=\t=
4d\tG:min7
2.d\t.
=\t=
1a\tC:7
*-\t*-
"""

STAFF_2 = """**kern\t**mxhm
*clefG2\t*
*k[]\t*
*M4/4\t*
=\t=
4r\tF:maj6
4f\t.
4f\t.
=\t=
2.g\tG:7
4b\t.
*-\t*-
"""


def test_conversion_applies_notation_rules():
    song = staves_to_song([STAFF_1, STAFF_2], "Demo")
    assert song["key"] == "F"  # the first system's signature wins the vote
    b = song["bars"]
    assert b[1]["notes"] == ["2.Eb4", "4Eb4"]  # the flat lasts through the bar
    assert [c["symbol"] for c in b[1]["chords"]] == ["A-7b5", "D7b9"]
    assert [c["beat"] for c in b[1]["chords"]] == [0, 2]  # chord inside the dotted half lands on beat 3
    assert b[5]["notes"] == ["2.G4", "4Bb4"]  # second system has no signature printed: page key still applies
    assert any("3 beats" in d for d in b[4]["doubts"])  # short bar is flagged
    assert song["line_breaks"] == [3, 5]
    from_dict(song)  # the draft must load as a song


def _render_lead_sheet(song_path: Path, out_png: Path):
    """Engrave a plain lead sheet (melody + chord names, no tab) to stand in for a scanned page."""
    s = load(song_path)
    from realbook.render_ly import ly_dur, ly_pitch

    mel = []
    for i, b in enumerate(s.bars):
        mel.append(" ".join(f"r{ly_dur(n.beats)}" if n.pitch is None else ly_pitch(n.pitch, 0) + ly_dur(n.beats)
                            + ("~" if n.tie else "") for n in b.notes) + (" \\break" if s.breaks_after(i) else " |"))
    ly = ('\\version "2.25.12"\n\\paper { indent = 0 tagline = ##f }\n'
          f'\\score {{ \\new Staff {{ \\key f \\major \\time 4/4 {" ".join(mel)} }} \\layout {{}} }}\n')
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "t.ly"
        src.write_text(ly, encoding="utf-8")
        subprocess.run([lilypond_exe(), "--png", "-dresolution=110", "-o", str(Path(td) / "t"), str(src)],
                       capture_output=True, check=True)
        Path(td, "t.png").replace(out_png)


def test_staves_found_on_an_engraved_page(tmp_path):
    png = tmp_path / "page.png"
    _render_lead_sheet(FIXTURE, png)
    page = Image.open(png)
    staves = find_staves(page)
    assert len(staves) == 5  # 20 bars, 4 per line
    for st in staves:
        c = crop(page, st)
        assert c.height > 5 * st.spacing and c.width > 0.5 * page.width


def test_recognizer_reads_a_staff(tmp_path):
    from realbook.omr.recognize import Recognizer

    png = tmp_path / "page.png"
    _render_lead_sheet(FIXTURE, png)
    page = Image.open(png)
    st = find_staves(page)[0]
    kern = Recognizer(threads=2).kern(crop(page, st))
    assert kern.startswith("**kern")
    song = staves_to_song([kern], "x")
    assert song["key"] == "F"
    assert len(song["bars"]) == 4
    # first bar of the test tune: rest, A, C, E
    assert song["bars"][0]["notes"] == ["4r", "4A4", "4C5", "4E5"]


def _title_over_blank_staff(page: Image.Image, spacing: float) -> Image.Image:
    """Put a printed blank staff with a big handwritten-style title across it above the music, like Real Book paper."""
    from PIL import ImageDraw, ImageFont

    w, h = page.size
    top_pad = int(14 * spacing)
    out = Image.new("L", (w, h + top_pad), 255)
    out.paste(page.convert("L"), (0, top_pad))
    d = ImageDraw.Draw(out)
    y0 = int(4 * spacing)
    for i in range(5):
        y = int(y0 + i * spacing)
        d.line([(int(0.08 * w), y), (int(0.92 * w), y)], fill=0, width=max(1, int(spacing / 8)))
    # like the real pages: marker lettering taller than the staff, straddling it (above the top line and
    # below the bottom line)
    font = ImageFont.load_default(size=int(9 * spacing))
    box = d.textbbox((0, 0), "ALONE TOGETHER", font=font)
    cap_top, cap_h = box[1], box[3] - box[1]
    y_text = int(y0 + 2 * spacing - cap_h / 2 - cap_top)
    d.text((int(0.15 * w), y_text), "ALONE TOGETHER", fill=0, font=font,
           stroke_width=max(2, int(spacing / 3)), stroke_fill=0)
    return out


def test_title_written_over_a_blank_staff_is_not_read_as_music(tmp_path):
    png = tmp_path / "page.png"
    _render_lead_sheet(FIXTURE, png)
    page = Image.open(png)
    sp = find_staves(page)[0].spacing
    with_title = _title_over_blank_staff(page, sp)
    from realbook.omr.recognize import Recognizer
    from realbook.omr.staves import looks_like_music

    rec = Recognizer(threads=2)
    staves = find_staves(with_title)
    kept = [st for st in staves if looks_like_music(st) or rec.kern_checked(crop(with_title, st))[1]]
    assert len(kept) == 5  # the title line is rejected, the 5 music lines stay
    assert kept[0].lines[0] > staves[0].lines[0]


def test_bracketed_melody_and_bass_staves_read_only_the_melody(tmp_path):
    ly = r'''\version "2.25.12"
\paper { indent = 0 tagline = ##f }
\score { \new StaffGroup <<
  \new Staff { \clef treble \key f \major r4 a' c'' e'' | ees''2. d''4 | bes'4 d''2. | c''1 \break
               r4 f' a' c'' | aes'2. g'4 | b'2. d''4 | d''2 e'2 }
  \new Staff { \clef bass \key f \major f,2. c4 | a,2. d4 | g,2 c2 | c1 \break f,1 | d1 | g,1 | c1 }
>> \layout {} }
'''
    src = tmp_path / "p.ly"
    src.write_text(ly, encoding="utf-8")
    subprocess.run([lilypond_exe(), "--png", "-dresolution=110", "-o", str(tmp_path / "p"), str(src)],
                   capture_output=True, check=True)
    staves = find_staves(Image.open(tmp_path / "p.png"))
    assert [st.role for st in staves] == ["melody", "lower", "melody", "lower"]
