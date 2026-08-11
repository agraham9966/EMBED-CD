"""Deterministic budget model self-check. Run: python tests/test_budget.py"""
import math

from tessera_paint import budget


def _square_bbox(lat, side_m):
    # build a roughly side_m x side_m bbox centered at (0, lat)
    dlat = (side_m / 2) / 111_320.0
    dlon = (side_m / 2) / (111_320.0 * math.cos(math.radians(lat)))
    return (-dlon, lat - dlat, dlon, lat + dlat)


def test_span_and_estimate():
    bbox = _square_bbox(48.0, 10_000)      # 10 km square at 48N
    wm, hm = budget.bbox_span_m(bbox)
    assert abs(wm - 10_000) < 200 and abs(hm - 10_000) < 200
    est = budget.estimate(bbox, detail=1)
    # 10 km / 10 m = 1000 px per side
    assert abs(est["px_w"] - 1000) <= 2 and abs(est["px_h"] - 1000) <= 2
    assert est["result_bytes"] == est["px_w"] * est["px_h"] * 128 * 4


def test_detail_shrinks_result_not_peak():
    bbox = _square_bbox(48.0, 10_000)
    e1 = budget.estimate(bbox, detail=1)
    e5 = budget.estimate(bbox, detail=5)
    assert e5["result_bytes"] < e1["result_bytes"] / 20   # ~25x smaller
    assert e5["peak_bytes"] == e1["peak_bytes"]           # peak is full-res, detail-independent


def test_max_box_side_and_fits():
    budget_bytes = 2 * 1024**3            # 2 GB
    side = budget.max_box_side_m(budget_bytes)
    # a box at exactly the max side should fit; 1.5x should not
    at = _square_bbox(0.0, side)
    over = _square_bbox(0.0, side * 1.5)
    assert budget.fits(at, 1, budget_bytes)
    assert not budget.fits(over, 1, budget_bytes)


if __name__ == "__main__":
    test_span_and_estimate()
    test_detail_shrinks_result_not_peak()
    test_max_box_side_and_fits()
    print("ok")
