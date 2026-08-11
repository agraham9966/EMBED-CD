"""Deterministic size/memory model. Embeddings are a fixed size — 10 m pixels, 128 float32
dims — so a bbox plus a detail (pooling) factor gives the exact array cost with no guessing.
Used to auto-size the region box and to reject too-big pins before any fetch.

Note on the PEAK: geotessera builds the full-resolution mosaic and reproject buffers before
pooling, so the transient peak is full-res regardless of detail. Until the fetch is rewritten
to stream+pool, `max_box_side_m` is governed by that full-res peak (detail-independent). Detail
still shrinks the *held* result (`result_bytes`), which is what lets two years coexist.
"""
import math

_BYTES_PER_PIXEL = 128 * 4      # a float32 128-d embedding vector
_NATIVE_M = 10.0                # TESSERA native pixel size
_PEAK_OVERHEAD = 2.5            # full-res mosaic + reproject/merge buffers (conservative)


def bbox_span_m(bbox):
    """Approx (width_m, height_m) of an EPSG:4326 bbox (min_lon,min_lat,max_lon,max_lat)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    height = abs(max_lat - min_lat) * 111_320.0
    mean_lat = math.radians((min_lat + max_lat) / 2.0)
    width = abs(max_lon - min_lon) * 111_320.0 * math.cos(mean_lat)
    return width, height


def estimate(bbox, detail=1):
    """Exact-ish cost of loading bbox at the given detail. Returns dict with output pixel dims,
    held result bytes (at detail), and the transient fetch peak bytes (full-res)."""
    detail = max(1, int(detail))
    px = _NATIVE_M * detail
    wm, hm = bbox_span_m(bbox)
    out_w = max(1, round(wm / px))
    out_h = max(1, round(hm / px))
    full_w = max(1, round(wm / _NATIVE_M))
    full_h = max(1, round(hm / _NATIVE_M))
    return {
        "px_w": out_w, "px_h": out_h,
        "result_bytes": out_w * out_h * _BYTES_PER_PIXEL,
        "peak_bytes": int(full_w * full_h * _BYTES_PER_PIXEL * _PEAK_OVERHEAD),
    }


def fits(bbox, detail, budget_bytes):
    """True if the fetch peak for this region fits the memory budget."""
    return estimate(bbox, detail)["peak_bytes"] <= budget_bytes


def max_box_side_m(budget_bytes):
    """Largest square region side (meters) whose full-res fetch peak fits the budget.
    Detail-independent for now (peak is full-res); see module note."""
    # peak = (side/10)^2 * 512 * overhead <= budget
    return _NATIVE_M * math.sqrt(max(budget_bytes, 1) / (_BYTES_PER_PIXEL * _PEAK_OVERHEAD))
