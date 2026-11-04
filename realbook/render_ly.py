"""Render a song to LilyPond: chord names + jazz voicing diagrams, melody staff, and tab."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .chords import Chord
from .engraver import lilypond_exe
from .pitch import Pitch
from .song import Song
from .tab import MelodyEvent, Placement, finger_melody
from .voicings import STYLES, TUNINGS, Voicing, voice_progression

LY_VERSION = "2.25.12"
NOTE_NAMES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]


@dataclass
class Arrangement:
    """Everything the renderer decided, kept so it can be verified independently."""

    song: Song
    style: str
    tuning_name: str
    tuning: list[int]
    voicings: list[tuple[int, Chord, Voicing]]  # (bar index, chord, voicing)
    melody: list[tuple[int, int, Pitch | None, int | None, tuple[int, int] | None]]
    # (bar, note index, written pitch, sounding midi, (string idx, fret))
    octave_shift: int  # semitones applied to the written melody to make it sound right on guitar
    positions: list  # hand position (index-finger fret) per melody event, None for rests
    open_strings: str = "avoid"


def arrange(song: Song, style: str = "drop3", tuning_name: str = "standard", melody_octave: str = "guitar",
            open_strings: str = "avoid", position: str | int = "auto") -> Arrangement:
    tuning = TUNINGS[tuning_name]
    evs = list(song.chord_events())
    voicings = voice_progression([ev.chord for _, ev in evs], style, tuning, allow_open=(open_strings == "allow"))
    # Guitar music is written an octave above where it sounds (treble clef with an 8 below).
    shift = -12 if melody_octave == "guitar" else 0
    flat = [(bi, ni, n) for bi, b in enumerate(song.bars) for ni, n in enumerate(b.notes)]
    midis = [n.pitch.midi + shift if n.pitch else None for _, _, n in flat]
    played = [m for m in midis if m is not None]
    # keep the line inside the instrument: move whole octaves if needed
    while played and min(played) < min(tuning):
        shift += 12
        midis = [m + 12 if m is not None else None for m in midis]
        played = [m for m in midis if m is not None]
    while played and max(played) > max(tuning) + 17:
        shift -= 12
        midis = [m - 12 if m is not None else None for m in midis]
        played = [m for m in midis if m is not None]
    events = [MelodyEvent(bi, m, n.beats, n.tie) for (bi, _, n), m in zip(flat, midis)]
    places = finger_melody(events, tuning, song.bars_per_line, open_strings, position)
    melody = [(bi, ni, n.pitch, m, pl.as_tuple() if pl else None) for (bi, ni, n), m, pl in zip(flat, midis, places)]
    return Arrangement(song, style, tuning_name, tuning, [(bi, ev.chord, v) for (bi, ev), v in zip(evs, voicings)],
                       melody, shift, [pl.position if pl else None for pl in places], open_strings)


# ---------------------------------------------------------------- LilyPond helpers

def ly_pitch(p: Pitch, semis_shift: int) -> str:
    acc = {"": "", "b": "es", "bb": "eses", "#": "is", "##": "isis"}[p.name.acc]
    if p.name.letter in "AE" and acc.startswith("es"):
        acc = acc[1:]  # LilyPond's Dutch names: as, es, ases, eses
    name = p.name.letter.lower() + acc
    octave = p.octave + semis_shift // 12
    marks = octave - 3
    return name + ("'" * marks if marks > 0 else "," * (-marks))


def roman(n: int) -> str:
    vals = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, r in vals:
        while n >= v:
            out += r
            n -= v
    return out


def ly_dur(beats: float) -> str:
    """Duration for a length in quarter-note beats: a plain value if one exists, else a scaled whole note."""
    from fractions import Fraction

    table = {4.0: "1", 3.0: "2.", 2.0: "2", 1.5: "4.", 1.0: "4", 0.75: "8.", 0.5: "8", 0.25: "16", 3.5: "2..",
             6.0: "1.", 1.75: "4..", 0.375: "16.", 0.125: "32", 7.0: "1..", 8.0: "\\breve"}
    for k, v in table.items():
        if abs(beats - k) < 1e-9:
            return v
    fr = Fraction(beats / 4).limit_denominator(96)
    return f"1*{fr.numerator}/{fr.denominator}"


def note_dur(note) -> str:
    return note.dur or ly_dur(note.beats)


def ly_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def chord_name_markup(ch: Chord) -> str:
    """Real Book style: bold root, smaller raised suffix, proper flat/sharp glyphs."""
    root = ch.root.letter
    acc = {"b": "\\flat", "#": "\\sharp", "": ""}[ch.root.acc]
    q = ch.quality
    q = re.sub(r"b(?=\d)", "♭", q)
    q = re.sub(r"#(?=\d)", "♯", q)
    parts = [f"\\bold {ly_str(root)}"]
    if acc:
        parts.append(f"\\raise #0.6 \\fontsize #-3 {acc}")
    if q:
        parts.append(f"\\raise #0.7 \\fontsize #-1.5 {ly_str(q)}")
    if ch.bass:
        b = ch.bass
        parts.append(f"{ly_str('/' + b.letter)}")
        if b.acc:
            glyph = "\\flat" if b.acc == "b" else "\\sharp"
            parts.append(f"\\raise #0.6 \\fontsize #-3 {glyph}")
    inner = "\\concat { " + " ".join(parts) + " }"
    if ch.optional:
        inner = f'\\concat {{ "(" {inner} ")" }}'
    return inner


DEG_DISPLAY = {"1": "R"}


def deg_label(d: str) -> str:
    d = DEG_DISPLAY.get(d, d)
    return d.replace("bb", "𝄫").replace("b", "♭").replace("#", "♯")


def diagram_markup(v: Voicing, n_strings: int) -> str:
    items = []
    for s, (f, d) in enumerate(zip(v.frets, v.degrees)):
        string_no = n_strings - s
        if f is None:
            items.append(f"(mute {string_no})")
        elif f == 0:
            items.append(f"(open {string_no})")
        else:
            items.append(f"(place-fret {string_no} {f} {ly_str(deg_label(d))})")
    fr = v.fretted
    count = max(4, (max(fr) - min(fr) + 1) if fr else 4)
    details = (f"(fret-diagram-details . ((finger-code . below-string) (fret-count . {count}) (number-type . arabic) "
               f"(label-dir . -1) (dot-radius . 0.34) (string-distance . 1.45) (string-label-font-mag . 0.5) (fret-label-font-mag . 0.5) "
               f"(top-fret-thickness . 3) (xo-font-magnification . 0.4)))")
    open_labels = [f"{deg_label(d)}" for f, d in zip(v.frets, v.degrees) if f == 0]
    diagram = f"\\override #'(size . 1.3) \\override #'{details} \\fret-diagram-verbose #'({' '.join(items)})"
    if open_labels:  # open strings get no label under the diagram; list them so nothing is unlabeled
        diagram = f"\\center-column {{ {diagram} \\fontsize #-4 {ly_str('open: ' + ' '.join(open_labels))} }}"
    return diagram


# Tab fret numbers: every note already carries the string we chose (\\N). LilyPond's own fret calculation
# misjudges some notes below middle C on Windows builds (rational arithmetic), rejecting valid strings and
# picking others. This replacement computes fret = note - open string in whole semitones, on our string.
FRET_FUNCTION = r"""#(define (rb-note-to-fret context notes specified-info . rest)
   (let* ((tunings (ly:context-property context 'stringTunings))
          (sevs (car specified-info)))
     (if (= (length sevs) (length notes))
         (map (lambda (note sev)
                (let* ((p (ly:event-property note 'pitch))
                       (n (and (not (null? sev)) (ly:event-property sev 'string-number #f)))
                       (s (if (and (integer? n) (<= 1 n (length tunings))) n 1))
                       (fret (- (ly:pitch-semitones p) (ly:pitch-semitones (list-ref tunings (1- s))))))
                  (list s fret)))
              notes sevs)
         (apply determine-frets context notes specified-info rest))))"""


def to_lilypond(arr: Arrangement) -> str:
    p = song_parts(arr)
    return f"""\\version "{LY_VERSION}"
{FRET_FUNCTION}
#(set-global-staff-size 17)
{paper_block(page_numbers=True)}
\\header {{
{p["header"]}
}}
{p["defs"]}
{p["score"]}
"""


def paper_block(page_numbers: bool = True) -> str:
    return f"""\\paper {{
  #(set-paper-size "a4")
  indent = 0
  top-margin = 12\\mm bottom-margin = 12\\mm left-margin = 14\\mm right-margin = 14\\mm
  system-system-spacing.basic-distance = #16
  markup-system-spacing.basic-distance = #10
  tagline = ##f
  print-page-number = {'##t' if page_numbers else '##f'}
}}"""


def song_parts(arr: Arrangement, uid: str = "", marker: str | None = None) -> dict:
    """The pieces of one lead sheet: header fields, music definitions and the score.

    uid (letters only) makes the music variable names unique so several songs can share one file;
    marker adds an invisible link to the title so a songbook can find where each song starts.
    """
    song = arr.song
    tuning = arr.tuning
    n = len(tuning)
    # ---- chord row
    per_bar = song.time[0] * 4 / song.time[1]
    vmap: dict[int, list] = {}
    for bi, ch, v in arr.voicings:
        vmap.setdefault(bi, []).append((ch, v))
    chord_row = []
    bar_times = song.bar_times()
    for bi, b in enumerate(song.bars):
        evs = b.chords
        items = list(zip(evs, vmap.get(bi, [])))
        bt = bar_times[bi]
        bar_len = song.pickup if (bi == 0 and song.pickup) else bt[0] * 4 / bt[1]
        pos = 0.0
        seg = []
        if not items or items[0][0].beat > 0:
            first = items[0][0].beat if items else bar_len
            seg.append(f"s{ly_dur(first)}")
            pos = first
        for k, (ev, (ch, v)) in enumerate(items):
            end = items[k + 1][0].beat if k + 1 < len(items) else bar_len
            mk = f"\\markup \\left-column {{ {chord_name_markup(ch)} \\vspace #0.2 {diagram_markup(v, n)} }}"
            seg.append(f"s{ly_dur(end - ev.beat)}^{mk}")
            pos = end
        prefix = f"\\time {b.time[0]}/{b.time[1]} " if b.time else ""
        chord_row.append(prefix + " ".join(seg) + " |")
    # ---- melody (sounding pitch; staff uses treble_8 so it reads at guitar pitch)
    shift = arr.octave_shift
    mel, mel_marked = [], []
    mi = 0
    prev_pos = None
    for bi, b in enumerate(song.bars):
        toks, toks_marked = [], []
        changes = []
        if b.time:
            changes.append(f"\\time {b.time[0]}/{b.time[1]}")
        if b.key:
            changes.append(f"\\key {ly_pitch(Pitch(b.key, 3), 0)} \\{b.mode or 'major'}")
        toks.extend(changes)
        toks_marked.extend(changes)
        open_tuplet = None
        for ni, note in enumerate(b.notes):
            _, _, p, midi, pl = arr.melody[mi]
            pos = arr.positions[mi]
            mi += 1
            # tuplets: open a \tuplet group when they start, close it when they end
            if note.tuplet != open_tuplet:
                if open_tuplet is not None:
                    toks.append("}")
                    toks_marked.append("}")
                if note.tuplet is not None:
                    toks.append(f"\\tuplet {note.tuplet[0]}/{note.tuplet[1]} {{")
                    toks_marked.append(toks[-1])
                open_tuplet = note.tuplet
            if p is None:
                toks.append(f"r{note_dur(note)}")
                toks_marked.append(toks[-1])
                continue
            s, f = pl
            tok = f"{ly_pitch(p, shift)}{note_dur(note)}\\{n - s}" + ("~" if note.tie else "")
            toks.append(tok)
            if pos != prev_pos:  # mark the hand position where it starts or changes, as guitar parts do
                tok += f'_\\markup {{ \\fontsize #-2 \\italic {ly_str("pos. " + roman(pos))} }}'
                prev_pos = pos
            toks_marked.append(tok)
        if open_tuplet is not None:
            toks.append("}")
            toks_marked.append("}")
        bar_end = " |"
        if b.double_bar_after:
            bar_end = ' \\bar "||"'
        if bi == len(song.bars) - 1:
            bar_end = ' \\bar "|."'
        brk = " \\break" if song.breaks_after(bi) else ""
        mel.append(" ".join(toks) + bar_end + brk)
        mel_marked.append(" ".join(toks_marked) + bar_end + brk)
    key_ly = ly_pitch(Pitch(song.key, 3), 0)
    partial = f"\\partial {ly_dur(song.pickup)}" if song.pickup else ""
    tuning_ly = " ".join(_ly_tuning_note(m) for m in tuning)
    tuning_txt = " ".join(NOTE_NAMES[m % 12] for m in tuning)
    clef = '"treble_8"' if shift <= -12 else '"treble"'
    key_txt = "Key of " + str(song.key).replace("b", "♭").replace("#", "♯") + (" minor" if song.mode == "minor" else "")
    sub = f"{song.style} · {key_txt}" if song.style else key_txt
    sub2 = f"{STYLES[arr.style]} · {arr.tuning_name.replace('-', ' ').capitalize()} tuning ({tuning_txt})"
    title = ly_str(song.title)
    if marker:
        title = f"\\markup \\with-url {ly_str(marker)} {ly_str(song.title)}"
    cr, me, mm = f"chordRow{uid}", f"melody{uid}", f"melodyMarked{uid}"
    header = f"""  title = {title}
  composer = {ly_str(song.composer)}
  subtitle = {ly_str(sub)}
  subsubtitle = {ly_str(sub2)}"""
    defs = f"""{cr} = {{ {partial} {' '.join(chord_row)} }}
{me} = {{ \\time {song.time[0]}/{song.time[1]} {partial} {' '.join(mel)} }}
{mm} = {{ \\time {song.time[0]}/{song.time[1]} {partial} {' '.join(mel_marked)} }}"""
    score = f"""\\score {{
  <<
    \\new Dynamics \\with {{
      \\override TextScript.self-alignment-X = #LEFT
      \\override TextScript.font-shape = #'upright
      \\override TextScript.font-series = #'medium
      \\override TextScript.font-size = #1
      \\override VerticalAxisGroup.staff-affinity = #DOWN
      \\override VerticalAxisGroup.nonstaff-relatedstaff-spacing.padding = #1
      \\textLengthOn
    }} \\{cr}
    \\new Staff \\with {{ \\omit StringNumber }} {{ \\clef {clef} \\key {key_ly} \\{song.mode} \\{mm} }}
    \\new TabStaff \\with {{ stringTunings = \\stringTuning <{tuning_ly}> noteToFretFunction = #rb-note-to-fret }} {{ \\{me} }}
  >>
  \\layout {{
    \\context {{ \\Score \\override SpacingSpanner.common-shortest-duration = #(ly:make-moment 1/2) }}
  }}
}}"""
    return {"header": header, "defs": defs, "score": score}


def _ly_tuning_note(midi: int) -> str:
    names = ["c", "cis", "d", "es", "e", "f", "fis", "g", "as", "a", "bes", "b"]
    octave = midi // 12 - 1
    marks = octave - 3
    return names[midi % 12] + ("'" * marks if marks > 0 else "," * (-marks))


# LilyPond prints UTF-8; on Windows also keep it from flashing a console window
_RUN = dict(capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0)


def compile_pdf(ly_source: str, out_pdf: Path, png_preview: Path | None = None, strict: bool = True) -> list[str]:
    """Engrave to PDF. strict=True turns any LilyPond warning into an error; otherwise warnings are returned."""
    exe = lilypond_exe()
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "song.ly"
        src.write_text(ly_source, encoding="utf-8")
        r = subprocess.run([exe, "-dno-point-and-click", "-o", str(Path(td) / "song"), str(src)], **_RUN)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-3000:])
        # any engraving warning (e.g. a note LilyPond can't put on the requested string) is treated as an error
        layout_only = ("unsupported formats", "compressing over-full page", "has been compressed")
        warnings = [l for l in r.stderr.splitlines()
                    if "warning" in l.lower() and not any(x in l for x in layout_only)]
        if warnings and strict:
            raise RuntimeError("LilyPond warnings:\n" + "\n".join(warnings[:20]))
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(Path(td, "song.pdf")), str(out_pdf))  # move, not rename: temp may be on another drive
        if png_preview:
            subprocess.run([exe, "--png", "-dresolution=120", "-o", str(Path(td) / "prev"), str(src)], **_RUN)
            pngs = sorted(Path(td).glob("prev*.png"))
            for i, p in enumerate(pngs):
                target = png_preview if i == 0 else png_preview.with_name(f"{png_preview.stem}-{i + 1}.png")
                shutil.move(str(p), str(target))
        return warnings
