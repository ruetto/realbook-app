"""Build a LilyPond file for one OpenBook tune that runs the event dumper over its chords and melody."""
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import os
OB = Path(os.environ.get("OPENBOOK_SRC", "openbook/src/openbook"))
HERE = Path(__file__).parent


def blocks(text: str) -> dict:
    out = {}
    for m in re.finditer(r'\{% if part == "(\w+)" %\}(.*?)\{% endif %\}', text, re.S):
        out[m.group(1)] = m.group(2)
    return out


def meta(text: str) -> dict:
    return tomllib.loads(re.match(r"\{#(.*?)#\}", text, re.S).group(1))


def build_ly(slug: str, dump_file: Path) -> tuple[str, dict]:
    text = (OB / f"{slug}.ly.tera").read_text(encoding="utf-8")
    md = meta(text)
    b = blocks(text)
    v = md.get("default_version", "Real")
    chords, voice = b["Chords" + v], b["Voice" + v]
    vars_ = b.get("Vars", "")
    ly = f"""\\include "{HERE / 'openbook_common.ly'}"
#(define dump-file-name "{dump_file}")
\\include "{HERE / 'dump.ly'}"
{vars_}
obChords = {{ {chords} }}
obVoice = \\relative c' {{ {voice} }}
\\score {{
  \\unfoldRepeats <<
    \\new ChordNames \\with {{ \\consists #(make-dumper "C") }} \\obChords
    \\new Staff \\with {{ \\consists #(make-dumper "M") }} {{ \\new Voice \\obVoice }}
  >>
  \\layout {{ }}
}}
"""
    return ly, md


def run(slug: str, workdir: Path) -> tuple[Path, dict, str]:
    workdir = workdir.resolve()  # LilyPond changes into the output folder, so paths must be absolute
    workdir.mkdir(parents=True, exist_ok=True)
    dump = workdir / f"{slug}.events"
    ly, md = build_ly(slug, dump)
    src = workdir / f"{slug}.ly"
    src.write_text(ly, encoding="utf-8")
    sys.path.insert(0, str(HERE.parents[1]))
    from realbook.engraver import lilypond_exe
    r = subprocess.run([lilypond_exe(), "-dno-print-pages", "-o", str(workdir / slug), str(src)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return dump, md, r.stderr


if __name__ == "__main__":
    d, md, err = run(sys.argv[1], Path("work"))
    print("\n".join(l for l in err.splitlines() if "rror" in l or "arning" in l)[:3000])
    print(d.read_text()[:4000])
