"""UDFs for the solar inspection labeling round-trip (pure Python, no table imports).

detect_anomalies finds hot cells (bright blobs) and shaded/soiled cells (dark blobs) on a grayscale
thermal-style panel image and returns COCO-style boxes. The other UDFs grade a panel, merge human
corrections over the automatic boxes, and score how much a reviewer changed.
"""

from __future__ import annotations

import json
from collections import deque

import numpy as np
import PIL.Image

import pixeltable as pxt

CELL = 8  # detection grid, in pixels
HOT_SIGMA = 6.0  # a cell is hot when its mean is this many std devs above the panel mean
DARK_SIGMA = 6.0  # ... and shaded/soiled when this many below
CATEGORIES = ('hotspot', 'shading')


def _blobs(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """4-connected components of a boolean grid -> (row0, col0, row1, col1, n_cells)."""
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    rows, cols = mask.shape
    for r in range(rows):
        for c in range(cols):
            if not mask[r, c] or seen[r, c]:
                continue
            q, cells = deque([(r, c)]), []
            seen[r, c] = True
            while q:
                y, x = q.popleft()
                cells.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < rows and 0 <= nx < cols and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
            ys, xs = zip(*cells)
            out.append((min(ys), min(xs), max(ys) + 1, max(xs) + 1, len(cells)))
    return out


@pxt.udf
def detect_anomalies(gray: PIL.Image.Image) -> pxt.Json:
    """Hot and shaded regions as COCO-style annotations: [{'bbox': [x, y, w, h], 'category': ..., 'score': ...}]."""
    a = np.asarray(gray.convert('L'), dtype=np.float32)
    h, w = (a.shape[0] // CELL) * CELL, (a.shape[1] // CELL) * CELL
    blocks = a[:h, :w].reshape(h // CELL, CELL, w // CELL, CELL).transpose(0, 2, 1, 3).reshape(h // CELL, w // CELL, -1)
    grid = np.median(blocks, axis=2)  # the median ignores 1-px cell gaps
    mu = float(np.median(grid))
    sd = 1.4826 * float(np.median(np.abs(grid - mu))) or 1.0  # robust spread (MAD)
    anns = []
    for category, mask in (('hotspot', grid > mu + HOT_SIGMA * sd), ('shading', grid < mu - DARK_SIGMA * sd)):
        for r0, c0, r1, c1, n in _blobs(mask):
            z = abs(float(grid[r0:r1, c0:c1].mean()) - mu) / sd
            anns.append({'bbox': [c0 * CELL, r0 * CELL, (c1 - c0) * CELL, (r1 - r0) * CELL],
                         'category': category, 'score': round(min(z / 20.0, 1.0), 3), 'cells': n})
    anns.sort(key=lambda d: (d['bbox'][1], d['bbox'][0]))
    return anns


@pxt.udf
def grade_panel(anns: pxt.Json) -> str:
    """ok / soiled / minor / major from the annotation list."""
    anns = anns or []
    hot = [d for d in anns if d.get('category') == 'hotspot']
    if any(d.get('cells', 1) >= 4 or d.get('score', 0) >= 0.8 for d in hot) or len(hot) >= 3:
        return 'major'
    if hot:
        return 'minor'
    return 'soiled' if anns else 'ok'


@pxt.udf
def count_category(anns: pxt.Json, category: str) -> int:
    return sum(1 for d in (anns or []) if d.get('category') == category)


@pxt.udf
def parse_boxes(text: str | None) -> pxt.Json:
    """Reviewer boxes arrive from SQL as JSON text; tolerate blanks and a bare list of [x, y, w, h]."""
    if text is None or not text.strip():
        return None
    val = json.loads(text)
    return [d if isinstance(d, dict) else {'bbox': list(d), 'category': 'hotspot'} for d in val]


@pxt.udf
def final_boxes(human: pxt.Json | None, auto: pxt.Json) -> pxt.Json:
    """Human-reviewed boxes win when present; otherwise the automatic ones."""
    src = human if human is not None else (auto or [])
    return [{'bbox': [int(v) for v in d['bbox']], 'category': d['category']} for d in src]


def _iou(a: list, b: list) -> float:
    ax1, ay1, ax2, ay2 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx1, by1, bx2, by2 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0, min(ax2, bx2) - max(ax1, bx1)), max(0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union else 0.0


@pxt.udf
def label_delta(auto: pxt.Json, human: pxt.Json | None) -> pxt.Json:
    """How a reviewer changed the automatic boxes: matched (IoU >= 0.5, same category), added, removed."""
    if human is None:
        return None
    auto, used, matched = list(auto or []), set(), 0
    for h in human:
        best, best_j = 0.0, None
        for j, a in enumerate(auto):
            if j in used or a['category'] != h['category']:
                continue
            iou = _iou(a['bbox'], h['bbox'])
            if iou > best:
                best, best_j = iou, j
        if best_j is not None and best >= 0.5:
            used.add(best_j)
            matched += 1
    return {'matched': matched, 'added': len(human) - matched, 'removed': len(auto) - len(used)}
