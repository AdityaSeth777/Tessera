import json
import shutil
import subprocess

import numpy as np
import pytest

from collage import render
from conftest import ROOT, needs_node


def test_cover_centres_and_fills():
    left, top, dw, dh, s = render.placement(100, 100, 200, 100, "cover", 1, 0, 0)
    assert (left, top, dw, dh, s) == (-50, 0, 200, 100, 1)


@pytest.mark.parametrize("pan, expected_left", [(1, 0), (-1, -100), (5, 0), (-5, -100), (0.5, -25)])
def test_cover_pan_is_fraction_of_slack_and_clamped(pan, expected_left):
    left, top, *_ = render.placement(100, 100, 200, 100, "cover", 1, pan, 0)
    assert left == pytest.approx(expected_left)
    assert top == 0


def test_contain_letterboxes():
    left, top, dw, dh, s = render.placement(100, 100, 200, 100, "contain", 1, 0, 1)
    assert (left, dw, dh, s) == (0, 100, 50, 0.5)
    assert top == pytest.approx(50)


def test_zoom_scales_and_opens_slack():
    left, top, dw, dh, s = render.placement(100, 100, 200, 100, "contain", 2, -1, 0)
    assert (dw, dh, s) == (200, 100, 1)
    assert left == pytest.approx(-100)


def test_visible_region_cover():
    t = {"fit": "cover", "zoom": 1, "panX": 0, "panY": 0}
    src, dst = render.visible_region(t, 100, 100, 200, 100)
    assert src == pytest.approx((50, 0, 150, 100))
    assert dst == (0, 0, 100, 100)


def test_visible_region_contain_is_letterboxed():
    t = {"fit": "contain", "zoom": 1, "panX": 0, "panY": 0}
    src, dst = render.visible_region(t, 100, 100, 200, 100)
    assert src == pytest.approx((0, 0, 200, 100))
    assert dst == (0, 25, 100, 50)


@pytest.mark.parametrize("aspect, edge, size", [
    ([1, 1], 1000, (1000, 1000)),
    ([4, 5], 1000, (800, 1000)),
    ([16, 9], 1001, (1000, 564)),
])
def test_canvas_size_is_even(aspect, edge, size):
    assert render.canvas_size(aspect, edge) == size


def test_clip_time_loops_from_start():
    m = {"duration": 4.0}
    assert render.clip_time(m, 1.5, 0) == 1.5
    assert render.clip_time(m, 1.5, 2.5) == pytest.approx(1.5)
    assert render.clip_time(m, 1.5, 3.0) == pytest.approx(2.0)
    assert render.clip_time(m, 5.0, 3.0) == 5.0


def test_video_duration_modes():
    tiles = [({"start": 0}, {"kind": "video", "duration": 4}), ({"start": 1}, {"kind": "video", "duration": 7}),
             ({}, {"kind": "image"})]
    assert render.video_duration(tiles, "longest", 0) == 6
    assert render.video_duration(tiles, "shortest", 0) == 4
    assert render.video_duration(tiles, "custom", 2.5) == 2.5
    assert render.video_duration([], "longest", 0) == 5.0


def test_rounded_mask_corners():
    m = np.asarray(render.rounded_mask(40, 30, 10))
    assert m[0, 0] == 0 and m[29, 39] == 0
    assert m[15, 20] == 255 and m[0, 20] == 255
    assert np.asarray(render.rounded_mask(10, 10, 0)).min() == 255


# ------------------------------------------------------------- JS / Python parity

PLACEMENT_CASES = [
    (tw, th, mw, mh, fit, zoom, px, py)
    for tw, th in ((100, 100), (300, 120))
    for mw, mh in ((4032, 3024), (1080, 1920))
    for fit in ("cover", "contain")
    for zoom in (1, 1.7)
    for px, py in ((0, 0), (0.4, -0.8), (3, -3))
]
CLIP_CASES = [(4.0, 0, 5.5), (4.0, 1.5, 6.9), (2.0, 0.9, 3.99), (1.0, 2.0, 7.0)]
TEMPLATE_COUNTS = {1: 1, 2: 2, 3: 10, 4: 18, 5: 36, 6: 52, 7: 74, 8: 94, 9: 106, 10: 106}


@pytest.fixture(scope="module")
def js_results(tmp_path_factory):
    d = tmp_path_factory.mktemp("js")
    shutil.copy(ROOT / "static" / "js" / "layout.js", d / "layout.mjs")
    runner = d / "run.mjs"
    runner.write_text(
        "import * as L from './layout.mjs';\n"
        f"const P = {json.dumps(PLACEMENT_CASES)};\n"
        f"const C = {json.dumps(CLIP_CASES)};\n"
        "console.log(JSON.stringify({\n"
        "  placement: P.map(a => { const r = L.placement(...a); return [r.left, r.top, r.dw, r.dh]; }),\n"
        "  clip: C.map(a => L.clipTime(...a)),\n"
        "  templates: Object.fromEntries([1,2,3,4,5,6,7,8,9,10].map(n => [n, L.templates(n).length])),\n"
        "}));\n"
    )
    out = subprocess.run(["node", str(runner)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@needs_node
def test_js_and_python_placement_match(js_results):
    for case, js in zip(PLACEMENT_CASES, js_results["placement"]):
        py = render.placement(*case)[:4]
        assert py == pytest.approx(js, abs=1e-9), case


@needs_node
def test_js_and_python_clip_time_match(js_results):
    for (dur, start, t), js in zip(CLIP_CASES, js_results["clip"]):
        assert render.clip_time({"duration": dur}, start, t) == pytest.approx(js, abs=1e-12)


@needs_node
def test_template_counts(js_results):
    assert {int(k): v for k, v in js_results["templates"].items()} == TEMPLATE_COUNTS
