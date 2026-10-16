<!-- pixeltable-example-app: 20261016-solar-inspection-labels -->
# Solar Panel Inspection labeling round-trip built with Pixeltable

![Solar Panel Inspection labeling round-trip built with Pixeltable](.github/social-preview.png)

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/pierrebrunelle/pixeltable-solar-inspection-labels?quickstart=1)
[![Built with Pixeltable](https://img.shields.io/badge/built%20with-Pixeltable-5b4bff)](https://pixeltable.com)
[![PyPI - pixeltable](https://img.shields.io/pypi/v/pixeltable?label=pixeltable)](https://pypi.org/project/pixeltable/)
[![GitHub stars](https://img.shields.io/github/stars/pixeltable/pixeltable?style=social)](https://github.com/pixeltable/pixeltable)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Solar Inspection Labels keeps a human-in-the-loop labeling loop inside Pixeltable tables. `Panels` holds thermal-style panel images; computed columns built from `pixeltable.functions.image` (`convert`, `resize`, `entropy`, `getextrema`) and a numpy UDF propose COCO-style hot-spot and shading boxes and grade each panel. `run.py` exports the review queue to SQLite with `pxt.io.export_sql` (boxes stay a JSON column), `reviewer.py` corrects labels in plain SQL like any SQL client or labeling tool would, and `pxt.io.import_sql(..., if_exists='append')` brings the decisions back into the `Reviews` table, whose computed column parses the reviewer's JSON. `batch_update` writes accepted labels onto `Panels`, so the final boxes and a per-panel reviewer delta (matched / added / removed by IoU) recompute automatically. Every step is a table version you can inspect or `revert()`, and `to_coco_dataset()` exports the corrected labels as a COCO dataset. The same `app.py` runs against a local database or a Pixeltable Cloud database. Drive it with `python run.py`; no HTTP router.

[Pixeltable](https://pixeltable.com) is open-source, Python-native **multimodal AI data infrastructure**: tables, incremental computed columns, UDFs, indexes and serving in one library, running locally or on Pixeltable Cloud.

> ⭐ **Like this example?** Star [pixeltable/pixeltable](https://github.com/pixeltable/pixeltable) on GitHub. It helps other developers find it.

## What this example shows

- **Incremental computed columns** powered by plain Python UDFs (`@pxt.udf`)
- **B-tree indexes** declared on the model (`__indexes__`) back the lookup queries
- **Multimodal media columns** with configurable media destinations
- **Versioning**: table history, snapshots and revert for an auditable trail
- **Importable UDF module**: UDFs live in `udfs.py`; tables and queries live in `app.py` (Pixeltable resolves UDFs by module path)
- **`pixeltable.toml`** declares a local database and a **Pixeltable Cloud** database, so the same code deploys with `pxt db update`

## The round-trip

| Step | Pixeltable API | Result |
|---|---|---|
| Propose labels | computed columns: `image.convert('L')`, `resize`, `entropy`, `getextrema`, `detect_anomalies` (UDF) | COCO-style boxes + a grade per panel |
| Export the queue | `pxt.io.export_sql(query, 'review_queue', db_connect_str='sqlite:///output/review.db')` | a SQL table; Json columns stay JSON |
| Review | `reviewer.py`, plain SQL (`INSERT INTO reviews ...`) | one row per panel and round |
| Re-import | `pxt.io.import_sql(select(reviews), engine, 'solar/reviews', if_exists='append')` | rows appended into a model-managed table; `boxes` parsed by a computed column |
| Apply | `Panels.batch_update([...])` | `boxes` and `delta` recompute; one table version per round |
| Undo | `panels.revert()` | the last round is gone; re-apply it |
| Export | `export_sql(..., if_exists='replace')`, `export_sql(..., if_exists='insert')`, `to_coco_dataset()` | `final_labels`, an append-only `label_runs` log and `output/solar_coco.json` |

## Notes from building it

- `import_sql` on a SQLite `TEXT PRIMARY KEY` column: SQLite reports the column as nullable, so pass `schema_overrides={'review_id': pxt.String}` together with `primary_key='review_id'` when creating a new table. Appending into an existing table (`if_exists='append'`) needs no overrides.
- Re-importing the same review rows fails with a duplicate primary key error and appends nothing, so a round can't be imported twice by accident.
- Bad reviewer JSON: `on_error='ignore'` keeps the row and records the error in `reviews.boxes.errortype`; the default `on_error='abort'` rejects the batch.
- `export_sql` refuses image columns (`Cannot export column of type Image`); export ids and labels, and keep images in Pixeltable.
- `export_parquet` wants a `pathlib.Path`, not a string.
- On a Pixeltable Cloud database `run.py` writes the COCO file from a query (`write_coco`), because `to_coco_dataset()` needs a local table on pixeltable 0.7.17.
- After changing a UDF's code, `pxt recompute solar/panels auto_boxes -f` refreshes the column and everything computed from it.

## Run the round-trip on Pixeltable Cloud

After `pxt db update` and `pxt schema update app.py pxt://<your-org>:<your-db>/solar` (see below), point `run.py` at the hosted tables:

```bash
SOLAR_CATALOG=pxt://<your-org>:<your-db>/solar python run.py
```

The images are uploaded on insert, the computed columns run in the hosted database, and `export_sql` / `import_sql` stream rows between your SQLite file and the cloud tables.

## What's inside

| File | What it is |
|------|------------|
| `.devcontainer/devcontainer.json` | GitHub Codespaces / Dev Container config: Python 3.12, installs `requirements.txt` |
| `.github/social-preview.png` | Social preview image (1280x640) |
| `CITATION.cff` | Citation metadata (authors, license, release date, keywords) |
| `app.py` | Tables declared as Python classes and `@pxt.query` functions |
| `pixeltable.toml` | Project config: the local database plus a Pixeltable Cloud database (sizing, deploy excludes) |
| `reviewer.py` | Play the human reviewer, in plain SQL, on the SQLite file Pixeltable exported |
| `run.py` | Drive the labeling round-trip end to end: seed -> export -> review in SQL -> re-import -> diff -> COCO |
| `seed.py` | Generate small synthetic thermal-style solar panel images and insert them into <catalog>/panels |
| `udfs.py` | Pixeltable UDFs (`@pxt.udf`) in their own importable module, imported by `app.py` |
| `requirements.txt` / `pyproject.toml` | Dependencies (`pixeltable>=0.7.17`) |

**Tables**

| Table | Stored columns | Computed columns |
|-------|---------|------------------|
| `panels` | `panel_id`, `array_id`, `string_no`, `captured_at`, `image`, `human_boxes`, `human_label`, `reviewed_by` | `gray`, `thumb`, `entropy`, `extrema`, `auto_boxes`, `auto_label`, `hot_cells`, `boxes`, `delta` |
| `reviews` | `review_id`, `panel_id`, `review_round`, `label`, `boxes_json`, `reviewer`, `reviewed_at`, `note` | `boxes` |

## Run in your browser (GitHub Codespaces)

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/pierrebrunelle/pixeltable-solar-inspection-labels?quickstart=1)

1. Click **Open in GitHub Codespaces** above (or [this link](https://codespaces.new/pierrebrunelle/pixeltable-solar-inspection-labels?quickstart=1)). The dev container installs Python 3.12 and `pixeltable>=0.7.17` from `requirements.txt`.
2. In the codespace terminal, create the tables, seed them and run the app:

   ```bash
   pxt schema update app.py solar
   python run.py                     # seed -> export_sql -> reviewer.py -> import_sql -> batch_update -> COCO
   python -c "import sqlite3; print(*sqlite3.connect('output/review.db').execute('select panel_id, label, reviewer, note from reviews'), sep='\n')"   # what the reviewers wrote
   pxt history solar/panels          # one version per labeling step
   ```


## Quickstart

Requires Python 3.11+ and `pixeltable>=0.7.17`.

```bash
git clone https://github.com/pierrebrunelle/pixeltable-solar-inspection-labels.git
cd pixeltable-solar-inspection-labels
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Create the tables in a local catalog directory named `solar`
pxt schema update app.py solar

python run.py                     # seed -> export_sql -> reviewer.py -> import_sql -> batch_update -> COCO
python -c "import sqlite3; print(*sqlite3.connect('output/review.db').execute('select panel_id, label, reviewer, note from reviews'), sep='\n')"   # what the reviewers wrote
pxt history solar/panels          # one version per labeling step
```

## Deploy to Pixeltable Cloud

The same `app.py` runs on [Pixeltable Cloud](https://pixeltable.com). Sign in (or get a free trial database with `pxt new`), point the second database entry in `pixeltable.toml` at your own database, then deploy:

```bash
pxt login                       # or: export PIXELTABLE_API_KEY=<your-api-key>
# edit pixeltable.toml: name = 'pxt://<your-org>:<your-db>'
pxt db update pxt://<your-org>:<your-db>                 # build the image and upload the project
pxt schema update app.py pxt://<your-org>:<your-db>/solar   # create the tables in the hosted database
```

Keep keys in environment variables or `pxt secret set`, never in code.

## Code walkthrough

**1. Business logic is plain Python, in `udfs.py`.** A `@pxt.udf` function can be used as a column expression. Pixeltable records UDFs by module path (`udfs.detect_anomalies`), so they live in their own importable module rather than inline in the app: the daemon, serving workers and Pixeltable Cloud import it again by that path.

```python
# udfs.py
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
```

**2. Tables are Python classes (`app.py`).** Annotated attributes are stored columns; attributes assigned an expression are **computed columns** (`gray`, `thumb`, `entropy`, `extrema`, `auto_boxes`, `auto_label`, `hot_cells`, `boxes`, `delta`), evaluated incrementally on every insert or update and recomputed when their inputs change. Indexes live next to the columns.

```python
# app.py
class Panels(TableModel, name='panels', has_default_idxs=False):
    """One inspection image per panel, with automatic and (after review) human labels."""

    panel_id = pxt.Column(type=pxt.String, primary_key=True)
    array_id: pxt.String
    string_no: pxt.Int
    captured_at: pxt.Timestamp
    image: pxt.Image
    human_boxes: pxt.Json | None
    human_label: pxt.String | None
    reviewed_by: pxt.String | None

    gray = image.convert('L')  # type: ignore[attr-defined]
    thumb = image.resize([160, 80])  # type: ignore[attr-defined]
    entropy = gray.entropy()
    extrema = gray.getextrema()
    auto_boxes = detect_anomalies(gray)
    auto_label = grade_panel(auto_boxes)
    hot_cells = count_category(auto_boxes, 'hotspot')
    boxes = final_boxes(human_boxes, auto_boxes)
    delta = label_delta(auto_boxes, human_boxes)

    __indexes__ = [pxt.BtreeIndex(array_id)]
```

**3. Queries are functions (`app.py`).** `@pxt.query` wraps a Pixeltable query so it can be called from Python or exposed as a route:

```python
# app.py
@pxt.query
def review_queue(min_hot: int = 0):
    """Panels whose automatic grade is not 'ok', worst first: what a reviewer should look at."""
    return (
        Panels.where((Panels.auto_label != 'ok') & (Panels.hot_cells >= min_hot))
        .order_by(Panels.hot_cells, asc=False)
        .select(Panels.panel_id, Panels.array_id, Panels.auto_label, Panels.hot_cells, Panels.auto_boxes)
    )
```

## Learn more

- 🌐 Website: https://pixeltable.com
- 📚 Docs: https://docs.pixeltable.com
- 💻 Source: https://github.com/pixeltable/pixeltable (⭐ star it if Pixeltable is useful to you)
- 📦 PyPI: https://pypi.org/project/pixeltable/
- 🧩 More example apps: https://pierrebrunelle.github.io/awesome-pixeltable-apps/

**[More Pixeltable example apps →](https://pierrebrunelle.github.io/awesome-pixeltable-apps/)**

---

<sub>Built as part of a daily series of Pixeltable example apps · Pixeltable 0.7.17 · Python, incremental computed columns · Licensed under Apache-2.0.</sub>
