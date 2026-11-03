"""LilyPond's Scheme engine (Guile) recompiles any source file that looks newer than its compiled
.go file. Packaging tools copy files and reset their dates, which triggers that and breaks startup.
Give every bundled LilyPond file a fixed old date, and the compiled .go files a later one."""
import os
import sys
from pathlib import Path

OLD = 1577836800  # 2020-01-01
NEWER = OLD + 86400


def main(dist: str) -> None:
    roots = list(Path(dist).rglob("lilypond-binaries"))
    if not roots:
        sys.exit("lilypond-binaries not found under " + dist)
    n = 0
    for root in roots:
        for p in root.rglob("*"):
            if p.is_file():
                t = NEWER if p.suffix == ".go" else OLD
                os.utime(p, (t, t))
                n += 1
    print(f"set dates on {n} files")


if __name__ == "__main__":
    main(sys.argv[1])
