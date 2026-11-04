"""Small lead-sheet images of each system, for the review screen (scan above, recognized music below).

Written pitch and plain treble clef, exactly like the Real Book page, so the two can be compared
by eye. Bars with review flags are drawn in orange. All systems of a song are engraved in one
LilyPond run (one \\book per system).
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .engraver import lilypond_exe
from .pitch import Pitch
from .render_ly import _RUN, chord_name_markup, ly_dur, ly_pitch, ly_str
from .song import Song, from_dict

FLAG_COLOR = "(rgb-color 0.85 0.45 0.0)"


def _bar_ly(song: Song, bi: int) -> tuple[str, str]:
    b = song.bars[bi]
    ts = song.bar_times()[bi]
    per_bar = song.pickup if (bi == 0 and song.pickup) else ts[0] * 4 / ts[1]
    notes = []
    for n in b.notes:
        if n.pitch is None:
            notes.append(f"r{n.dur or ly_dur(n.beats)}" + (f"*{n.tuplet[1]}/{n.tuplet[0]}" if n.tuplet else ""))
        else:
            notes.append(f"{ly_pitch(n.pitch, 0)}{n.dur or ly_dur(n.beats)}" + ("~" if n.tie else "")
                         + (f"*{n.tuplet[1]}/{n.tuplet[0]}" if n.tuplet else ""))
    total = b.beats
    if total < per_bar:  # keep later bars aligned; the bar is flagged anyway
        gap = per_bar - total
        for d in (4.0, 3.0, 2.0, 1.5, 1.0, 0.5, 0.25):
            while gap >= d - 1e-9:
                notes.append(f"s{ly_dur(d)}")
                gap -= d
    body = " ".join(notes) or f"s{ly_dur(per_bar)}"
    if b.doubts:
        body = (f"\\override NoteHead.color = #{FLAG_COLOR} \\override Stem.color = #{FLAG_COLOR} "
                f"\\override Accidental.color = #{FLAG_COLOR} {body} \\revert NoteHead.color \\revert Stem.color "
                f"\\revert Accidental.color")
    # chord row
    pos, seg = 0.0, []
    evs = sorted(b.chords, key=lambda e: e.beat)
    if not evs or evs[0].beat > 0:
        first = evs[0].beat if evs else per_bar
        seg.append(f"s{ly_dur(first)}")
        pos = first
    for k, ev in enumerate(evs):
        end = evs[k + 1].beat if k + 1 < len(evs) else per_bar
        mk = chord_name_markup(ev.chord)
        if b.doubts:
            mk = f"\\with-color #{FLAG_COLOR} {mk}"
        seg.append(f"s{ly_dur(end - ev.beat)}^\\markup {mk}")
    return body, " ".join(seg)


def system_previews(song_data: dict, out_dir: Path, resolution: int = 100) -> list[Path]:
    """One PNG per system (as split in song_data['systems'] or 4 bars per line)."""
    song = from_dict(song_data)
    systems = song_data.get("systems") or [
        {"first_bar": i, "last_bar": min(i + 3, len(song.bars) - 1)} for i in range(0, len(song.bars), 4)]
    key = ly_pitch(Pitch(song.key, 3), 0)
    books = []
    for i, sy in enumerate(systems):
        mel, chords = [], []
        for bi in range(sy["first_bar"], sy["last_bar"] + 1):
            m, c = _bar_ly(song, bi)
            mel.append(m + " |")
            chords.append(c + " |")
        bar_no = sy["first_bar"] + 1
        omit_ts = "" if i == 0 else "\\omit Staff.TimeSignature"
        books.append(f"""\\book {{
  \\bookOutputSuffix "s{i:03d}"
  \\score {{
    <<
      \\new Dynamics \\with {{ \\override TextScript.self-alignment-X = #LEFT \\override TextScript.font-size = #1.5 \\override TextScript.font-shape = #'upright \\override TextScript.font-series = #'medium \\textLengthOn }}
        {{ {' '.join(chords)} }}
      \\new Staff {{ \\set Score.currentBarNumber = #{bar_no} \\override Score.BarNumber.break-visibility = ##(#f #t #t)
        \\clef treble \\key {key} \\{song.mode} \\time {song.time[0]}/{song.time[1]} {omit_ts} {' '.join(mel)} \\bar "|" }}
    >>
    \\layout {{ indent = 0 ragged-right = ##f line-width = 190\\mm }}
  }}
}}""")
    src = f"""\\version "2.25.12"
#(set-global-staff-size 20)
\\paper {{ tagline = ##f print-page-number = ##f oddHeaderMarkup = ##f evenHeaderMarkup = ##f }}
{chr(10).join(books)}
"""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "prev.ly"
        f.write_text(src, encoding="utf-8")
        subprocess.run([lilypond_exe(), "--png", "-dcrop", f"-dresolution={resolution}",
                        "-o", str(Path(td) / "prev"), str(f)], **_RUN)
        out = []
        for i in range(len(systems)):
            cands = sorted(Path(td).glob(f"prev-s{i:03d}*.png"))
            cropped = [c for c in cands if "cropped" in c.name] or cands
            if not cropped:
                out.append(None)
                continue
            target = out_dir / f"system_{i:03d}.png"
            target.write_bytes(cropped[0].read_bytes())
            out.append(target)
    return out


__all__ = ["system_previews", "ly_str"]
