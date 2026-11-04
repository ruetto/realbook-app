"""Independent check: LilyPond's own MIDI of each tune's melody vs. the converted song, note by note."""
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction as F
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
WORK = Path("work/midi")
SONGS = Path("pack/songs")


def midi_notes(slug):
    import extract
    import mido

    from realbook.engraver import lilypond_exe

    text = (extract.OB / f"{slug}.ly.tera").read_text(encoding="utf-8")
    md = extract.meta(text)
    b = extract.blocks(text)
    v = md.get("default_version", "Real")
    ly = f"""\\include "{extract.HERE / 'openbook_common.ly'}"
{b.get("Vars", "")}
obVoice = \\relative c' {{ {b["Voice" + v]} }}
\\score {{ \\unfoldRepeats \\new Staff \\new Voice \\obVoice \\midi {{ }} }}
"""
    work = WORK.resolve()
    work.mkdir(parents=True, exist_ok=True)
    src = work / f"{slug}.ly"
    src.write_text(ly, encoding="utf-8")
    subprocess.run([lilypond_exe(), "-o", str(work / slug), str(src)], capture_output=True)
    mf = mido.MidiFile(str(work / f"{slug}.midi"))
    tpq = mf.ticks_per_beat
    notes = []
    for tr in mf.tracks:
        t, on = 0, {}
        for msg in tr:
            t += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                on[msg.note] = t
            elif msg.type in ("note_off", "note_on") and msg.note in on:
                st = on.pop(msg.note)
                notes.append((F(st, tpq), msg.note, F(t - st, tpq)))
    notes.sort()
    return notes


def song_notes(song):
    from realbook.song import from_dict

    s = from_dict(song)
    t = F(0)
    out = []
    pend = None  # tied note being extended
    bar_times = s.bar_times()
    for bi, (b, ts) in enumerate(zip(s.bars, bar_times)):
        for n in b.notes:
            d = F(n.beats).limit_denominator(1000)
            if n.pitch is None:
                t += d
                continue
            if pend and pend[1] == n.pitch.midi and pend[0] + pend[2] == t:
                pend = (pend[0], pend[1], pend[2] + d)
            else:
                if pend:
                    out.append(pend)
                pend = (t, n.pitch.midi, d)
            if not n.tie:
                out.append(pend)
                pend = None
            t += d
    if pend:
        out.append(pend)
    return out


def one(slug):
    try:
        song = json.loads((SONGS / f"{slug}.json").read_text())
        m = midi_notes(slug)
        # chords in the melody (double stops): keep the top note, as the converter does
        top = {}
        for st, p, d in m:
            if st not in top or p > top[st][1]:
                top[st] = (st, p, d)
        m = sorted(top.values())
        # the lead sheet keeps one line: drop notes that start while the kept note still sounds (second voices)
        mono, end = [], None
        for st, p, d in m:
            if end is not None and st < end:
                continue
            mono.append((st, p, d))
            end = st + d
        m = mono
        # grace notes have (near) zero length in MIDI: the converter leaves them out on purpose
        m = [x for x in m if x[2] >= F(1, 48)]
        s = song_notes(song)
        mism = [(a, b) for a, b in zip(m, s) if a != b]
        return {"slug": slug, "midi": len(m), "song": len(s), "mismatch": len(mism) + abs(len(m) - len(s)),
                "first": [(str(a), str(b)) for a, b in mism[:3]]}
    except Exception as e:
        return {"slug": slug, "error": str(e)[:300]}


if __name__ == "__main__":
    slugs = sys.argv[1:] or sorted(p.stem for p in SONGS.glob("*.json"))
    with ProcessPoolExecutor(4) as ex:
        res = list(ex.map(one, slugs, chunksize=4))
    Path("work/verify_report.json").write_text(json.dumps(res, indent=1))
    exact = sum(1 for r in res if r.get("mismatch") == 0)
    print(f"{len(res)} tunes checked, {exact} identical to LilyPond's MIDI note for note")
    for r in res[:5] if len(res) <= 5 else []:
        print(r)
