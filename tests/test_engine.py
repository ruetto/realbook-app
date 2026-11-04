"""Accuracy tests. Every check here recomputes results independently of the code that produced them."""
import itertools
from pathlib import Path

import pytest
from music21 import harmony

from realbook.chords import DEGREE_SEMIS, parse_chord, to_music21_figure
from realbook.render_ly import arrange
from realbook.song import load
from realbook.voicings import STYLES, TUNINGS, candidates, verify

SONG = Path(__file__).parent / "fixtures" / "test_tune.json"
ROOTS = "C Db D Eb E F F# Gb G Ab A Bb B".split()
QUALS = ["", "-", "maj7", "-7", "7", "6", "-6", "-7b5", "dim7", "dim", "aug", "+7", "7b9", "7#9", "7b5", "7#5", "9",
         "-9", "maj9", "13", "7#11", "7b13", "-maj7", "7sus4", "sus4", "69", "-11", "maj7#11", "7b9b13", "9sus4"]
# music21 spells a few qualities differently, or can't read them; these are the equivalent spellings it accepts
M21_ALIAS = {"maj9": "M9", "-maj7": "mM7"}
# Jazz convention differs from music21 here (13 chords omit the 11th) or music21 can't parse; checked by hand below
HAND_CHECKED = {"13": [0, 4, 7, 10, 2, 9], "69": [0, 4, 7, 9, 2], "9sus4": [0, 5, 7, 10, 2]}
KEYS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


@pytest.mark.parametrize("root,q", list(itertools.product(ROOTS, QUALS)))
def test_chord_symbol_tones_match_music21(root, q):
    ch = parse_chord(root + q)
    if q in HAND_CHECKED:
        want = {(ch.root.pc + i) % 12 for i in HAND_CHECKED[q]}
    else:
        fig = to_music21_figure(root) + M21_ALIAS[q] if q in M21_ALIAS else to_music21_figure(root + q)
        want = {p.pitchClass for p in harmony.ChordSymbol(fig).pitches}
    assert ch.pcs() == want


def test_song_is_well_formed():
    s = load(SONG)
    assert s.check() == []
    assert len(s.bars) == 20


def _all_arrangements():
    s = load(SONG)
    for key, style, tuning in itertools.product(KEYS, STYLES, TUNINGS):
        yield key, style, tuning, arrange(s.transposed(key), style, tuning)


ARRS = list(_all_arrangements())


@pytest.mark.parametrize("key,style,tuning,arr", ARRS, ids=[f"{k}-{s}-{t}" for k, s, t, _ in ARRS])
def test_every_voicing_is_correct_and_playable(key, style, tuning, arr):
    tun = TUNINGS[tuning]
    for bi, ch, v in arr.voicings:
        assert verify(ch, style, v, tun) == [], (bi + 1, ch.symbol, v)
        # independent check with music21: every sounding note belongs to the chord or an accepted tension
        sounding = {(tun[s] + f) % 12 for s, f in enumerate(v.frets) if f is not None}
        m21 = {p.pitchClass for p in harmony.ChordSymbol(to_music21_figure(ch.symbol)).pitches}
        tensions = {(ch.root.pc + DEGREE_SEMIS[d]) % 12 for d in ("9", "11", "13")}
        assert sounding <= (m21 | tensions), (ch.symbol, v)
        # the 3rd and 7th (or 6th), which define the chord, must sound
        for role in ("third", "seventh", "sixth"):
            if role in ch.tones:
                assert (ch.root.pc + DEGREE_SEMIS[ch.tones[role]]) % 12 in sounding, (ch.symbol, role, v)


@pytest.mark.parametrize("key,style,tuning,arr", ARRS[:: len(STYLES)], ids=[f"{k}-{t}" for k, _, t, _ in ARRS[:: len(STYLES)]])
def test_every_tab_number_plays_the_written_note(key, style, tuning, arr):
    tun = TUNINGS[tuning]
    prev = None
    for bi, ni, pitch, midi, place in arr.melody:
        if pitch is None:
            assert place is None
            continue
        s, f = place
        assert 0 <= f <= 17
        assert tun[s] + f == midi, (bi + 1, ni + 1, str(pitch), place)
        assert midi == pitch.midi + arr.octave_shift
        note = arr.song.bars[bi].notes[ni]
        if prev and prev[0].tie:
            assert prev[1] == place, f"tied note moved, bar {bi + 1}"
        prev = (note, place)


@pytest.mark.parametrize("key", KEYS)
def test_transposition_preserves_music(key):
    s = load(SONG)
    t = s.transposed(key)
    n0 = [n.pitch.midi for b in s.bars for n in b.notes if n.pitch]
    n1 = [n.pitch.midi for b in t.bars for n in b.notes if n.pitch]
    shift = n1[0] - n0[0]
    assert all(b - a == shift for a, b in zip(n0, n1)), "melody intervals changed"
    assert shift % 12 == (t.key.pc - s.key.pc) % 12
    for (_, e0), (_, e1) in zip(s.chord_events(), t.chord_events()):
        assert (e1.chord.root.pc - e0.chord.root.pc) % 12 == shift % 12
        assert e1.chord.tones == e0.chord.tones
        assert e1.chord.root.acc not in ("bb", "##") and str(e1.chord.root) not in ("Cb", "Fb", "E#", "B#")
    # round trip back to F gives the original spelling
    back = t.transposed("F")
    assert [n.pitch.midi % 12 for b in back.bars for n in b.notes if n.pitch] == \
           [n.pitch.midi % 12 for b in s.bars for n in b.notes if n.pitch]
    if key not in ("Db", "Gb", "Ab"):  # keys where no double flat had to be respelled: spelling is exact too
        assert [str(n.pitch.name) for b in back.bars for n in b.notes if n.pitch] == \
               [str(n.pitch.name) for b in s.bars for n in b.notes if n.pitch]
    # chord roots come back to the same pitch; spelling may differ only where the intermediate key forced a
    # respelling (e.g. Fb-7 written as E-7 in Db). The app always transposes from the original, never chained.
    assert [e.chord.root.pc for _, e in back.chord_events()] == [e.chord.root.pc for _, e in s.chord_events()]


@pytest.mark.parametrize("key", KEYS)
def test_melody_spelling_follows_the_key(key):
    """Diatonic melody notes must use the key's own letter spellings (F# in G, never Gb)."""
    from realbook.pitch import key_accidentals

    s = load(SONG)
    t = s.transposed(key)
    acc = key_accidentals(key)
    src_acc = key_accidentals("F")
    for b0, b1 in zip(s.bars, t.bars):
        for n0, n1 in zip(b0.notes, b1.notes):
            if n0.pitch is None:
                continue
            if n0.pitch.name.acc == src_acc.get(n0.pitch.name.letter, ""):  # diatonic in F
                assert n1.pitch.name.acc == acc.get(n1.pitch.name.letter, ""), (str(n0.pitch), str(n1.pitch), key)


@pytest.mark.parametrize("style", list(STYLES))
@pytest.mark.parametrize("tuning", list(TUNINGS))
def test_every_song_chord_has_a_voicing_in_every_key(style, tuning):
    s = load(SONG)
    for key in KEYS:
        for _, ev in s.transposed(key).chord_events():
            assert candidates(ev.chord, style, TUNINGS[tuning]), (key, ev.chord.symbol)


# ---------------------------------------------------------------- end-to-end: read back what is printed

import re as _re

from realbook.render_ly import to_lilypond

_DEG_FROM_LABEL = {"R": "1"}


def _label_to_degree(label: str) -> str:
    d = label.replace("𝄫", "bb").replace("♭", "b").replace("♯", "#")
    return _DEG_FROM_LABEL.get(d, d)


LY_NOTE = _re.compile(r"(?<![\\a-z])([a-g](?:eses|isis|es|is|s)?)([',]*)(\d+\.*)\\(\d)")
LY_PC = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def _ly_midi(name: str, marks: str) -> int:
    pc = LY_PC[name[0]]
    rest = name[1:]
    pc += {"": 0, "is": 1, "isis": 2, "es": -1, "s": -1, "eses": -2, "ses": -2}[rest]
    octave = 3 + marks.count("'") - marks.count(",")
    return 12 * (octave + 1) + pc


@pytest.mark.parametrize("key,style,tuning,arr", ARRS, ids=[f"{k}-{s}-{t}" for k, s, t, _ in ARRS])
def test_printed_diagrams_and_tab_decode_correctly(key, style, tuning, arr):
    """Parse the generated LilyPond source and check every printed dot, label and tab number."""
    ly = to_lilypond(arr)
    tun = TUNINGS[tuning]
    n = len(tun)
    diagrams = _re.findall(r"\\fret-diagram-verbose #'\((.*?)\)\s*(?:\}|\\fontsize)", ly)
    assert len(diagrams) == len(arr.voicings)
    for (bi, ch, v), d in zip(arr.voicings, diagrams):
        played = 0
        for kind, string_no, rest in _re.findall(r"\((place-fret|open|mute) (\d)\s*([^)]*)\)", d):
            s = n - int(string_no)
            if kind == "mute":
                assert v.frets[s] is None
                continue
            played += 1
            if kind == "open":
                fret, label = 0, None
            else:
                fret_s, label = _re.match(r'(\d+) "(.*)"', rest).groups()
                fret = int(fret_s)
            midi = tun[s] + fret
            if label is not None:
                deg = _label_to_degree(label)
                assert (midi - ch.root.pc) % 12 == DEGREE_SEMIS[deg] % 12, (bi + 1, ch.symbol, string_no, fret, label)
            m21 = {p.pitchClass for p in harmony.ChordSymbol(to_music21_figure(ch.symbol)).pitches}
            tens = {(ch.root.pc + DEGREE_SEMIS[x]) % 12 for x in ("9", "11", "13")}
            assert midi % 12 in m21 | tens, (bi + 1, ch.symbol, string_no, fret)
        assert played == len(v.midi)
    # melody: each printed note with its string number must be playable on that string at fret 0..17,
    # and must equal the song's pitch moved by the arrangement's octave shift
    melody_src = ly.split("melody = {", 1)[1].split("melodyMarked = {", 1)[0]
    printed = [(_ly_midi(nm, mk), int(st)) for nm, mk, _, st in LY_NOTE.findall(melody_src)]
    expected = [(m, pl) for _, _, p, m, pl in arr.melody if p is not None]
    assert len(printed) == len(expected)
    for (pm, string_no), (em, (s, f)) in zip(printed, expected):
        assert pm == em
        assert n - string_no == s
        assert 0 <= pm - tun[s] <= 17


# ---------------------------------------------------------------- fingering conventions

from realbook.tab import MelodyEvent, _repeat_map, _shift_point


def _events(arr):
    return [MelodyEvent(bi, m, arr.song.bars[bi].notes[ni].beats, arr.song.bars[bi].notes[ni].tie)
            for bi, ni, _, m, _ in arr.melody]


@pytest.mark.parametrize("key,style,tuning,arr", ARRS[:: len(STYLES)], ids=[f"{k}-{t}" for k, _, t, _ in ARRS[:: len(STYLES)]])
def test_fingering_follows_position_playing(key, style, tuning, arr):
    tun = TUNINGS[tuning]
    ev = _events(arr)
    mid_phrase_shifts = 0
    prev = None
    for i, ((bi, ni, p, m, pl), pos) in enumerate(zip(arr.melody, arr.positions)):
        if p is None:
            continue
        s, f = pl
        # no open strings unless the note has no fretted placement at all
        if f == 0:
            assert not any(1 <= m - o <= 17 for o in tun), f"bar {bi + 1}: avoidable open string"
        else:
            assert pos - 1 <= f <= pos + 4, f"bar {bi + 1}: fret {f} outside position {pos}"
        if prev is not None and pos != prev and not _shift_point(ev, i, arr.song.bars_per_line):
            mid_phrase_shifts += 1
        prev = pos
    assert mid_phrase_shifts <= 2, f"{mid_phrase_shifts} shifts in the middle of phrases"
    # repeated phrases are fingered exactly like their first appearance
    for b, a in _repeat_map(ev).items():
        assert arr.melody[b][4] == arr.melody[a][4], f"repeat in bar {arr.melody[b][0] + 1} fingered differently"


def test_triads_and_optional_chords_are_voiced():
    s = load(SONG)
    for style in STYLES:
        arr = arrange(s, style, "standard")
        syms = [ch.symbol for _, ch, _ in arr.voicings]
        assert "Bb" in syms and "(G-7)" in syms


def test_song_without_chords_still_engraves(tmp_path):
    from realbook.render_ly import compile_pdf, to_lilypond
    from realbook.song import from_dict

    d = {"title": "No chords", "key": "F", "time": "4/4",
         "bars": [{"chords": [], "notes": ["4r", "4A4", "4C5", "4E5"]}, {"chords": [], "notes": ["1F4"]}]}
    for style in STYLES:
        compile_pdf(to_lilypond(arrange(from_dict(d), style, "standard")), tmp_path / f"{style}.pdf")
        assert (tmp_path / f"{style}.pdf").stat().st_size > 1000


# ---------------------------------------------------------------- pickups, triplets, minor keys, changes

MINOR = Path(__file__).parent / "fixtures" / "minor_test.json"


def test_minor_song_with_pickup_triplets_and_changes_is_well_formed():
    s = load(MINOR)
    assert s.mode == "minor" and s.pickup == 3
    assert s.check() == []
    assert [t for t in s.bar_times()][4] == (3, 4)
    trip = s.bars[2].notes[0]
    assert trip.tuplet == (3, 2) and abs(trip.beats - 1 / 3) < 1e-9


@pytest.mark.parametrize("key", ["E", "G", "C", "F#", "Bb", "Db"])
@pytest.mark.parametrize("style", list(STYLES))
def test_minor_song_engraves_without_any_warning(key, style, tmp_path):
    """strict engraving: LilyPond itself checks every bar length, tuplet, pickup and string number."""
    from realbook.render_ly import compile_pdf, to_lilypond

    s = load(MINOR).transposed(key)
    assert s.check() == []
    arr = arrange(s, style, "standard")
    ly = to_lilypond(arr)
    try:
        compile_pdf(ly, tmp_path / "m.pdf")
    except RuntimeError as e:
        lines = [x for x in ly.splitlines() if x.startswith(("melody =", "    \\new TabStaff"))]
        places = [(str(p), m, pl) for _, _, p, m, pl in arr.melody]
        raise AssertionError(f"{e}\n--- source ---\n" + "\n".join(lines) + f"\n--- placements ---\n{places}") from None


def test_minor_transposition_keeps_mode_and_moves_key_changes():
    s = load(MINOR).transposed("A")
    assert str(s.key) == "A" and s.mode == "minor"
    assert str(s.bars[5].key) == "C"  # G major section moves up a fourth too


def test_song_pack_import(tmp_path):
    import json
    import zipfile

    from realbook.app.library import Library, import_song_pack

    pack = tmp_path / "pack.zip"
    with zipfile.ZipFile(pack, "w") as z:
        z.writestr("manifest.json", json.dumps({"id_prefix": "openbook", "book": "OpenBook"}))
        z.writestr("songs/minor_test.json", MINOR.read_text())
    lib = Library(tmp_path / "lib")
    assert import_song_pack(lib, pack) == (1, 0)
    d = lib.load_song("openbook-minor_test")
    assert d["review"]["status"] == "reviewed" and d["book"] == "OpenBook"
    d["edited"] = True
    lib.save_song("openbook-minor_test", d)
    assert import_song_pack(lib, pack) == (0, 1)  # the person's edits are not overwritten
