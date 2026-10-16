"""Drive the labeling round-trip end to end: seed -> export -> review in SQL -> re-import -> diff -> COCO.

    pxt schema update app.py solar
    python run.py
    SOLAR_CATALOG=pxt://<org>:<db> python run.py    # the same round-trip against a Pixeltable Cloud database

Steps
 1. seed.py inserts 12 panel images; computed columns grade them and propose boxes.
 2. export_sql writes the review queue (Json boxes included) to output/review.db (SQLite).
 3. reviewer.py corrects the labels in plain SQL (round 1: the queue; round 2: a spot check of 'ok' panels).
 4. import_sql appends the reviewer decisions into solar/reviews (if_exists='append').
 5. batch_update writes the accepted labels onto solar/panels; final boxes + the reviewer delta recompute.
 6. Table versions show each step; revert() undoes the spot-check round and it is re-applied.
 7. export_sql writes final_labels (replace) and appends a run summary (insert); to_coco_dataset
    exports the corrected labels as a COCO dataset (output/solar_coco.json).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import sqlalchemy as sa

import pixeltable as pxt

HERE = Path(__file__).parent
OUT = HERE / 'output'
DB = OUT / 'review.db'
SQLITE = f'sqlite:///{DB}'
CATALOG = os.environ.get('SOLAR_CATALOG', 'solar')  # a local dir, or pxt://<org>:<db> after `pxt schema update app.py pxt://<org>:<db>`


def step(title: str) -> None:
    print(f'\n== {title}')


def import_round(engine: sa.Engine, review_round: int) -> pxt.Table:
    reviews_sql = sa.Table('reviews', sa.MetaData(), autoload_with=engine)
    sel = sa.select(reviews_sql).where(reviews_sql.c.review_round == review_round)
    return pxt.io.import_sql(sel, engine, f'{CATALOG}/reviews', if_exists='append')


def apply_round(panels: pxt.Table, reviews: pxt.Table, review_round: int) -> int:
    rows = reviews.where(reviews.review_round == review_round).select(
        reviews.panel_id, reviews.label, reviews.boxes, reviews.reviewer).collect()
    updates = [{'panel_id': r['panel_id'], 'human_boxes': r['boxes'], 'human_label': r['label'],
                'reviewed_by': r['reviewer']} for r in rows]
    if updates:
        panels.batch_update(updates)
    return len(updates)


def write_coco(panels: pxt.Table, dest: Path) -> None:
    """COCO json from a query, for hosted tables: images are saved next to the json."""
    img_dir = dest.parent / 'coco_images'
    img_dir.mkdir(exist_ok=True)
    images, annotations, categories = [], [], {}
    for i, r in enumerate(panels.order_by(panels.panel_id).select(panels.panel_id, panels.image, panels.boxes).collect()):
        path = img_dir / f"{r['panel_id']}.png"
        r['image'].save(path)
        images.append({'id': i, 'file_name': str(path), 'width': r['image'].width, 'height': r['image'].height})
        for b in r['boxes']:
            cid = categories.setdefault(b['category'], len(categories))
            x, y, w, h = b['bbox']
            annotations.append({'id': len(annotations), 'image_id': i, 'category_id': cid, 'bbox': [x, y, w, h],
                                'area': w * h, 'iscrowd': 0})
    dest.write_text(json.dumps({'images': images, 'annotations': annotations,
                                'categories': [{'id': c, 'name': n} for n, c in categories.items()]}))


def main() -> None:
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    engine = sa.create_engine(SQLITE)
    panels, reviews = pxt.get_table(f'{CATALOG}/panels'), pxt.get_table(f'{CATALOG}/reviews')
    print(f'catalog: {CATALOG}')

    step('1. seed')
    subprocess.run([sys.executable, str(HERE / 'seed.py')], check=True)
    for r in panels.group_by(panels.auto_label).select(panels.auto_label, n=pxt.functions.count(panels.panel_id)).collect():
        print(f"  auto_label {r['auto_label']:<7} {r['n']}")

    step('2. export the review queue to SQLite')
    queue = (panels.where(panels.auto_label != 'ok').order_by(panels.hot_cells, asc=False)
             .select(panels.panel_id, panels.array_id, panels.auto_label, panels.hot_cells, panels.auto_boxes))
    pxt.io.export_sql(queue, 'review_queue', db_connect_str=SQLITE, if_exists='replace')
    with engine.connect() as c:
        cols = {col['name']: str(col['type']) for col in sa.inspect(engine).get_columns('review_queue')}
        print(f"  review_queue: {c.execute(sa.text('select count(*) from review_queue')).scalar()} rows, columns {cols}")

    step('3+4+5. round 1: review in SQL, re-import, apply')
    subprocess.run([sys.executable, str(HERE / 'reviewer.py'), '--round', '1', '--db', str(DB)], check=True)
    import_round(engine, 1)
    print(f'  {CATALOG}/reviews rows: {reviews.count()}; applied {apply_round(panels, reviews, 1)} to panels')

    step('round 2: spot-check the panels graded ok')
    ok = panels.where(panels.auto_label == 'ok').select(panels.panel_id, panels.array_id, panels.auto_boxes)
    pxt.io.export_sql(ok, 'spot_check', db_connect_str=SQLITE, if_exists='replace')
    subprocess.run([sys.executable, str(HERE / 'reviewer.py'), '--round', '2', '--db', str(DB)], check=True)
    import_round(engine, 2)
    v_before_r2 = panels.get_metadata()['version']
    print(f'  applied {apply_round(panels, reviews, 2)} (panels version {v_before_r2} -> {panels.get_metadata()["version"]})')

    step('6. diff: what the reviewers changed')
    for r in (panels.where(panels.human_label != None)  # noqa: E711
              .order_by(panels.panel_id)
              .select(panels.panel_id, panels.auto_label, panels.human_label, panels.delta, panels.reviewed_by).collect()):
        flag = '' if r['delta'] == {'matched': r['delta']['matched'], 'added': 0, 'removed': 0} else '  <- corrected'
        print(f"  {r['panel_id']}  {r['auto_label']:>6} -> {r['human_label']:<6} {r['delta']} by {r['reviewed_by']}{flag}")

    step('6b. revert the spot-check round, then re-apply it')
    panels.revert()
    spot = panels.where(panels.reviewed_by == 'dev').count()
    print(f"  after revert: version {panels.get_metadata()['version']}, panels reviewed by dev: {spot}")
    apply_round(panels, reviews, 2)
    spot = panels.where(panels.reviewed_by == 'dev').count()
    print(f"  re-applied:   version {panels.get_metadata()['version']}, panels reviewed by dev: {spot}")

    step('7. export final labels + COCO dataset')
    final = panels.order_by(panels.panel_id).select(
        panels.panel_id, panels.array_id, label=panels.human_label, auto_label=panels.auto_label,
        boxes=panels.boxes, delta=panels.delta, reviewed_by=panels.reviewed_by)
    pxt.io.export_sql(final, 'final_labels', db_connect_str=SQLITE, if_exists='replace')
    summary = panels.where(panels.human_label == 'major').select(
        n_major=pxt.functions.count(panels.panel_id), hot_cells=pxt.functions.sum(panels.hot_cells))
    pxt.io.export_sql(summary, 'label_runs', db_connect_str=SQLITE, if_exists='insert', if_not_exists='create')
    if CATALOG.startswith('pxt:'):  # a Pixeltable Cloud database
        # to_coco_dataset() only runs on local tables (pixeltable 0.7.17); build the same file from a query.
        write_coco(panels, OUT / 'solar_coco.json')
    else:
        coco_path = panels.select({'image': panels.image, 'annotations': panels.boxes}).to_coco_dataset()
        shutil.copy(coco_path, OUT / 'solar_coco.json')
    coco = json.loads((OUT / 'solar_coco.json').read_text())
    cats = {c['id']: c['name'] for c in coco['categories']}
    per_cat = {name: sum(1 for a in coco['annotations'] if cats[a['category_id']] == name) for name in cats.values()}
    print(f"  COCO: {len(coco['images'])} images, {len(coco['annotations'])} annotations, per category {per_cat}")
    with engine.connect() as c:
        print(f"  final_labels {c.execute(sa.text('select count(*) from final_labels')).scalar()} rows; "
              f"label_runs {c.execute(sa.text('select count(*) from label_runs')).scalar()} rows")

    step('history')
    for v in panels.get_versions()[:6]:
        print(f"  v{v['version']:<3} {v.get('change_type', '')!s:<7} inserted={v.get('inserts', '')} updated={v.get('updates', '')} deleted={v.get('deletes', '')}")
    print(f'\ndone in {time.time() - t0:.1f}s')


if __name__ == '__main__':
    main()
