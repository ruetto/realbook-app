"""Entry point for the packaged app.

RealBookGuitar.exe              opens the app
RealBookGuitar.exe --selftest   checks the packaged engraver and recognizer, writes selftest.txt, exits 0/1
"""
import sys


def selftest() -> int:
    import json
    import subprocess
    import tempfile
    import traceback
    from pathlib import Path

    log = []
    ok = True
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    out = Path(sys.executable).parent / "selftest.txt" if getattr(sys, "frozen", False) else Path("selftest.txt")
    try:
        from PIL import Image

        from realbook.engraver import lilypond_exe
        from realbook.omr.kern2song import staves_to_song
        from realbook.omr.recognize import Recognizer
        from realbook.omr.staves import crop, find_staves
        from realbook.render_ly import _RUN, arrange, compile_pdf, ly_dur, ly_pitch, to_lilypond
        from realbook.song import load

        fixture = base / "fixtures" / "test_tune.json"
        if not fixture.exists():
            fixture = Path(__file__).parent / "tests" / "fixtures" / "test_tune.json"
        song = load(fixture)
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "t.pdf"
            compile_pdf(to_lilypond(arrange(song, "drop3", "standard")), pdf)
            log.append(f"engrave: ok ({pdf.stat().st_size} bytes)")
            mel = " ".join(" ".join(f"r{ly_dur(n.beats)}" if n.pitch is None else ly_pitch(n.pitch, 0) + ly_dur(n.beats)
                                    for n in b.notes) + " |" for b in song.bars[:4])
            src = Path(td) / "p.ly"
            src.write_text('\\version "2.25.12"\n\\paper { indent = 0 tagline = ##f }\n'
                           f'\\score {{ \\new Staff {{ \\key f \\major \\time 4/4 {mel} }} \\layout {{}} }}\n',
                           encoding="utf-8")
            subprocess.run([lilypond_exe(), "--png", "-dresolution=110", "-o", str(Path(td) / "p"), str(src)], **_RUN)
            page = Image.open(Path(td) / "p.png")
            st = find_staves(page)
            kern = Recognizer().kern(crop(page, st[0]))
            got = staves_to_song([kern], "x")["bars"][0]["notes"]
            log.append(f"recognize: bar 1 read as {got}")
            if got != ["4r", "4A4", "4C5", "4E5"]:
                ok = False
                log.append("recognize: UNEXPECTED RESULT")
        import PySide6.QtPdf  # noqa: F401
        import PySide6.QtPrintSupport  # noqa: F401

        log.append("qt: ok")
    except Exception:
        ok = False
        log.append(traceback.format_exc())
    out.write_text("\n".join(log) + ("\nPASS\n" if ok else "\nFAIL\n"), encoding="utf-8")
    print("\n".join(log))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    from realbook.app.main import main

    main()
