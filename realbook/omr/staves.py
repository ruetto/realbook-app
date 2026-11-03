"""Find the music lines (staves) on a scanned lead-sheet page and crop each one with its chord row.

Classical method, no neural network: staff lines are long horizontal runs of ink, so the
row-wise ink profile shows five evenly spaced peaks per staff. Each crop is fitted to the
staff lines with room above for the chord symbols, which is what the recognizer expects.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass
class Staff:
    lines: list[int]  # y of the five staff lines (page pixels)
    spacing: float
    x0: int
    x1: int
    crop_box: tuple[int, int, int, int]  # left, top, right, bottom


def _peaks(profile: np.ndarray, thresh: float) -> list[int]:
    rows = np.where(profile > thresh)[0]
    out, cur = [], []
    for r in rows:
        if cur and r - cur[-1] > 2:
            out.append(int(round(np.mean(cur))))
            cur = []
        cur.append(r)
    if cur:
        out.append(int(round(np.mean(cur))))
    return out


def _five_line_groups(peaks: list[int]) -> list[list[int]]:
    groups, i = [], 0
    while i <= len(peaks) - 5:
        run = peaks[i:i + 5]
        d = np.diff(run)
        if d.min() >= 3 and d.max() - d.min() <= max(3, 0.25 * d.mean()):
            groups.append(run)
            i += 5
        else:
            i += 1
    return groups


def find_staves(page: Image.Image, chord_room: float = 6.5, below: float = 3.0) -> list[Staff]:
    gray = np.asarray(page.convert("L"))
    ink = gray < 150
    h, w = ink.shape
    # Scans are often slightly warped, so staff lines drift across the page. Look for five-line
    # groups in several vertical strips separately (lines are nearly flat within a strip), then merge.
    found: list[list[int]] = []
    for a, b in ((0.05, 0.3), (0.25, 0.5), (0.45, 0.7), (0.65, 0.9), (0.1, 0.9)):
        band = ink[:, int(a * w): int(b * w)]
        profile = band.sum(1).astype(float)
        best: list[list[int]] = []
        for frac in (0.5, 0.4, 0.3, 0.22):
            g = _five_line_groups(_peaks(profile, frac * band.shape[1]))
            if len(g) > len(best):
                best = g
        found.extend(best)
    found.sort(key=lambda g: g[0])
    clusters: list[list[list[int]]] = []
    for g in found:
        sp = float(np.mean(np.diff(g)))
        if clusters and abs(np.mean(clusters[-1][-1]) - np.mean(g)) < 2 * sp:
            clusters[-1].append(g)
        else:
            clusters.append([g])
    staves = []
    for cl in clusters:
        s = float(np.median([np.mean(np.diff(g)) for g in cl]))
        lines = [int(round(np.median([g[i] for g in cl]))) for i in range(5)]
        top_line = min(g[0] for g in cl)
        bot_line = max(g[-1] for g in cl)
        # horizontal extent: columns with ink on most staff-line rows (allowing for warp)
        line_ink = np.zeros(w, dtype=int)
        tol = max(2, int(round(0.45 * s)))  # warp tolerance: lines may drift up to about half a space
        for r in lines:
            line_ink += ink[max(0, r - tol): r + tol + 1, :].any(0)
        cols = np.where(line_ink >= 4)[0]
        if len(cols) < 0.2 * w:
            continue
        # ignore stray ink: keep the longest run of staff columns (gaps under 3 spacings are barlines etc.)
        runs, start = [], cols[0]
        for c0, c1 in zip(cols, cols[1:]):
            if c1 - c0 > 3 * s:
                runs.append((start, c0))
                start = c1
        runs.append((start, cols[-1]))
        x0, x1 = max(runs, key=lambda r: r[1] - r[0])
        top = int(max(0, top_line - chord_room * s))
        bottom = int(min(h, bot_line + below * s))
        staves.append(Staff(lines, s, int(x0), int(x1), (max(0, int(x0) - 4), top, min(w, int(x1) + 4), bottom)))
    return staves


def crop(page: Image.Image, st: Staff) -> Image.Image:
    return page.convert("L").crop(st.crop_box)
