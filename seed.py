"""Generate small synthetic thermal-style solar panel images and insert them into <catalog>/panels
(SOLAR_CATALOG, default 'solar').

Each panel is 312x160 px: a 6x12 grid of cells, mild sensor noise, and a few known defects
(hot cells, a soiled/shaded strip, sun glare at an edge, a subtle hot cell, a hairline crack
the detector cannot see). The ground truth is
written to output/truth.json so reviewer.py can play a human reviewer.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import numpy as np
import PIL.Image

import pixeltable as pxt

W, H = 312, 160
OUT = Path(__file__).parent / 'output'
IMG_DIR = OUT / 'images'
CATALOG = os.environ.get('SOLAR_CATALOG', 'solar')

# panel_id -> list of (kind, x, y, w, h, delta)  kind in hotspot / shading / glare / subtle
DEFECTS = {
    'A1-01': [],
    'A1-02': [('hotspot', 104, 48, 24, 24, 95)],
    'A1-03': [('shading', 0, 112, 312, 32, -70)],
    'A1-04': [('hotspot', 208, 24, 16, 16, 90), ('hotspot', 48, 104, 16, 16, 85)],
    'B2-01': [('glare', 288, 0, 24, 24, 110)],
    'B2-02': [],
    'B2-03': [('hotspot', 152, 72, 40, 32, 100)],
    'B2-04': [('subtle', 56, 32, 16, 16, 14), ('hotspot', 240, 104, 16, 16, 90)],
    'C3-01': [('shading', 120, 0, 48, 160, -60)],
    'C3-02': [('hotspot', 16, 16, 16, 16, 88), ('hotspot', 136, 64, 16, 16, 92), ('hotspot', 264, 120, 16, 16, 96)],
    'C3-03': [('crack', 60, 80, 120, 3, -70)],
    'C3-04': [('hotspot', 200, 56, 24, 24, 80), ('shading', 0, 0, 72, 40, -65)],
}


def render(panel_id: str, defects: list) -> PIL.Image.Image:
    rng = np.random.default_rng(sum(map(ord, panel_id)))
    v = np.full((H, W), 110.0) + rng.normal(0, 3.0, (H, W))
    v[::26, :] -= 25  # cell gaps (horizontal)
    v[:, ::26] -= 25  # cell gaps (vertical)
    for _kind, x, y, w, h, d in defects:
        yy, xx = np.mgrid[0:H, 0:W]
        cx, cy = x + w / 2, y + h / 2
        fall = np.exp(-(((xx - cx) / (w / 1.6)) ** 2 + ((yy - cy) / (h / 1.6)) ** 2))
        box = (xx >= x) & (xx < x + w) & (yy >= y) & (yy < y + h)
        v += np.where(box, d, d * 0.35 * fall)
    v = np.clip(v, 0, 255)
    rgb = np.stack([v, v * 0.6, 255 - v], axis=-1).astype(np.uint8)  # monotonic false-color "thermal" palette
    return PIL.Image.fromarray(rgb, 'RGB')


def main() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    t = pxt.get_table(f'{CATALOG}/panels')
    if t.count() > 0:
        print(f'solar/panels already has {t.count()} rows; skipping seed')
        return
    base = dt.datetime(2026, 10, 16, 11, 0, tzinfo=dt.timezone.utc)
    rows, truth = [], {}
    for i, (pid, defects) in enumerate(DEFECTS.items()):
        path = IMG_DIR / f'{pid}.png'
        render(pid, defects).save(path)
        rows.append({'panel_id': pid, 'array_id': pid[:2], 'string_no': int(pid[-2:]),
                     'captured_at': base + dt.timedelta(minutes=3 * i), 'image': str(path)})
        truth[pid] = [{'bbox': [x, y, w, h], 'category': {'shading': 'shading', 'crack': 'crack'}.get(k, 'hotspot')}
                      for k, x, y, w, h, _ in defects if k != 'glare']
    status = t.insert(rows)
    (OUT / 'truth.json').write_text(json.dumps(truth, indent=1))
    print(f'inserted {status.num_rows} panels ({status.num_excs} errors)')


if __name__ == '__main__':
    main()
