"""Play the human reviewer, in plain SQL, on the SQLite file Pixeltable exported.

Round 1 reviews every panel in `review_queue` (exported by run.py); round 2 spot-checks the
panels the detector graded 'ok' (`spot_check`). Decisions go into a `reviews` table that run.py
re-imports with pxt.io.import_sql. A real team would use any SQL client or labeling tool here;
the "reviewer's eyes" in this demo are output/truth.json, written by seed.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

OUT = Path(__file__).parent / 'output'
SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id    TEXT PRIMARY KEY,
    panel_id     TEXT NOT NULL,
    review_round INTEGER NOT NULL,
    label        TEXT NOT NULL,
    boxes_json   TEXT,
    reviewer     TEXT NOT NULL,
    reviewed_at  TIMESTAMP NOT NULL,
    note         TEXT
)"""


def grade(boxes: list[dict]) -> str:
    hot = [b for b in boxes if b['category'] == 'hotspot']
    if any(b['bbox'][2] * b['bbox'][3] >= 256 for b in hot) or len(hot) >= 3:
        return 'major'
    if hot:
        return 'minor'
    return 'defect' if boxes else 'ok'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--round', type=int, choices=[1, 2], required=True)
    ap.add_argument('--db', default=str(OUT / 'review.db'))
    args = ap.parse_args()
    truth = json.loads((OUT / 'truth.json').read_text())
    con = sqlite3.connect(args.db)
    con.execute(SCHEMA)
    src, reviewer = ('review_queue', 'maria') if args.round == 1 else ('spot_check', 'dev')
    cols = [r[1] for r in con.execute(f'PRAGMA table_info({src})')]
    rows = [dict(zip(cols, r)) for r in con.execute(f'SELECT * FROM {src} ORDER BY panel_id')]
    now = dt.datetime(2026, 10, 16, 17, 0) + dt.timedelta(hours=args.round)  # naive: SQLite has no time zones
    n_changed = 0
    for row in rows:
        auto = json.loads(row['auto_boxes']) if isinstance(row['auto_boxes'], str) else row['auto_boxes']
        human = truth[row['panel_id']]
        same = sorted(json.dumps(b['bbox']) + b['category'] for b in human) == sorted(
            json.dumps(b['bbox']) + b['category'] for b in auto)
        n_changed += not same
        note = None if same else f"auto {len(auto)} box(es) -> {len(human)} after review"
        con.execute('INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?,?,?)',
                    (f"{row['panel_id']}-r{args.round}", row['panel_id'], args.round, grade(human),
                     json.dumps(human), reviewer, now.isoformat(sep=' '), note))
    con.commit()
    print(f'round {args.round}: {reviewer} reviewed {len(rows)} panels from {src}, corrected {n_changed}')


if __name__ == '__main__':
    main()
