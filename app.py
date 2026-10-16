"""Solar Inspection Labels: a labeling round-trip for solar panel inspection images, in Pixeltable.

Panels holds thermal-style panel images. Computed columns derive a grayscale copy, a thumbnail,
image statistics and automatic hot-spot / shading boxes (COCO-style) with a grade per panel.
run.py exports the review queue to SQLite with pxt.io.export_sql, lets a reviewer correct the
labels in plain SQL, re-imports them with pxt.io.import_sql into Reviews, writes the accepted
labels back onto Panels (final boxes and the reviewer delta recompute automatically) and exports
a COCO dataset of the corrected labels with to_coco_dataset.

Layout: UDFs live in udfs.py; this file holds the table models and @pxt.query functions.
"""

from __future__ import annotations

import pixeltable as pxt

from udfs import count_category, detect_anomalies, final_boxes, grade_panel, label_delta, parse_boxes

TableModel = pxt.model_base()


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

    __indexes__ = [pxt.BtreeIndex(array_id)]  # type: ignore[name-defined]  # noqa: F821


class Reviews(TableModel, name='reviews', has_default_idxs=False):
    """Reviewer decisions re-imported from SQL (one row per panel per review round)."""

    review_id = pxt.Column(type=pxt.String, primary_key=True)
    panel_id: pxt.String
    review_round: pxt.Int
    label: pxt.String
    boxes_json: pxt.String | None
    reviewer: pxt.String
    reviewed_at: pxt.Timestamp
    note: pxt.String | None

    boxes = parse_boxes(boxes_json)


@pxt.query
def review_queue(min_hot: int = 0):
    """Panels whose automatic grade is not 'ok', worst first: what a reviewer should look at."""
    return (
        Panels.where((Panels.auto_label != 'ok') & (Panels.hot_cells >= min_hot))
        .order_by(Panels.hot_cells, asc=False)
        .select(Panels.panel_id, Panels.array_id, Panels.auto_label, Panels.hot_cells, Panels.auto_boxes)
    )


@pxt.query
def array_panels(array_id: str):
    """Every panel of one array with its current (final) label."""
    return (
        Panels.where(Panels.array_id == array_id)
        .order_by(Panels.string_no)
        .select(Panels.panel_id, Panels.string_no, Panels.auto_label, Panels.human_label, Panels.boxes)
    )


@pxt.query
def reviews_of_round(review_round: int):
    """Reviewer decisions of one round, as imported from SQL."""
    return Reviews.where(Reviews.review_round == review_round).select(
        Reviews.panel_id, Reviews.label, Reviews.boxes, Reviews.reviewer, Reviews.reviewed_at
    )
