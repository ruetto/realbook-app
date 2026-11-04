"""Build the OpenBook song pack: convert every tune, verify it, and zip the songs that pass.

Usage (from this folder):
    git clone --depth 1 https://github.com/veltzer/openbook.git openbook
    pip install mido
    python build_pack.py                # all tunes
    python build_pack.py 20             # first 20 only (quick try)

Output:
    pack/songs/<tune>.json              converted songs
    work/report.json                    per-tune results
    openbook-songpack.zip               load it in the app with File > Add OpenBook tunes

A tune goes into the pack only if all of these hold:
  * LilyPond read its source without errors,
  * every chord name parses back to exactly the notes OpenBook wrote for that chord,
  * every bar adds up to its time signature,
  * its melody matches LilyPond's own MIDI of the tune note for note (pitch, start, length).
The tune data stays on your computer: it is not committed to the app's repository.
"""
import json
import sys
import traceback
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

PACK = Path("pack/songs")
WORK = Path("work")


def convert_one(slug: str) -> dict:
    import convert
    import extract
    import verify
    from realbook.song import from_dict

    r = {"slug": slug, "ok": False}
    try:
        ev, md, err = extract.run(slug, WORK / "events")
        r["lily_errors"] = [line for line in err.splitlines() if "error" in line.lower()][:5]
        if not ev.exists() or ev.stat().st_size == 0:
            r["fail"] = "LilyPond produced no events"
            return r
        song, probs = convert.convert(ev, md)
        song["openbook"] = {"slug": slug, "completion": md.get("attributes", {}).get("completion", ""),
                            "version": md.get("default_version", "Real")}
        r["chord_problems"] = probs[:20]
        r["check"] = from_dict(song).check()[:20]
        PACK.mkdir(parents=True, exist_ok=True)
        (PACK / f"{slug}.json").write_text(json.dumps(song, ensure_ascii=False, indent=1), encoding="utf-8")
        v = verify.one(slug)
        r["midi_mismatch"] = v.get("mismatch", v.get("error"))
        r["midi_first"] = v.get("first", [])
        r["ok"] = (not r["lily_errors"] and not probs and not r["check"] and v.get("mismatch") == 0)
        r["title"], r["bars"] = song["title"], len(song["bars"])
        r["completion"] = song["openbook"]["completion"]
    except Exception:
        r["fail"] = traceback.format_exc()[-1500:]
    return r


def main():
    src = Path(__import__("os").environ.get("OPENBOOK_SRC", "openbook/src/openbook"))
    slugs = sorted(p.name[: -len(".ly.tera")] for p in src.glob("*.ly.tera"))
    if len(sys.argv) > 1:
        slugs = slugs[: int(sys.argv[1])]
    WORK.mkdir(exist_ok=True)
    with ProcessPoolExecutor() as ex:
        res = list(ex.map(convert_one, slugs, chunksize=4))
    (WORK / "report.json").write_text(json.dumps(res, indent=1))
    good = [r for r in res if r["ok"]]
    with zipfile.ZipFile("openbook-songpack.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({
            "id_prefix": "openbook", "book": "OpenBook",
            "source": "https://github.com/veltzer/openbook (tunes remain the property of their copyright holders)",
            "songs": len(good)}))
        for r in good:
            z.write(PACK / f"{r['slug']}.json", f"songs/{r['slug']}.json")
    print(f"{len(res)} tunes: {len(good)} passed every check and are in openbook-songpack.zip; "
          f"{len(res) - len(good)} left out (see work/report.json)")


if __name__ == "__main__":
    main()
