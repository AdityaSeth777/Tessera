import numpy as np
import pytest

from collage import color
from conftest import IS_MACOS


def grey(v):
    return np.array([[v, v, v]], dtype=float)


def test_hlg_reference_white_round_trip():
    signal = color.nits_to_hlg(np.array([203.0, 203.0, 203.0]))
    assert signal == pytest.approx([0.7499] * 3, abs=0.001)
    nits = color._hlg_to_nits(grey(float(signal[0])), "bt2020")
    assert nits[0] == pytest.approx([203.0] * 3, abs=0.5)


def test_hlg_endpoints():
    assert color.nits_to_hlg(np.zeros(3)) == pytest.approx([0, 0, 0], abs=1e-6)
    assert color._hlg_to_nits(grey(1.0), "bt2020")[0] == pytest.approx([1000.0] * 3, rel=1e-3)


@pytest.mark.parametrize("hlg, sdr_signal", [(0.0, 0.0), (0.75, 0.67), (1.0, 1.0)])
def test_bt2446a_reference_points(hlg, sdr_signal):
    nits = color._hlg_to_nits(grey(hlg), "bt2020")
    lin = color.bt2446a(nits, color.HLG_NOMINAL_PEAK)
    assert color._srgb_oetf(lin)[0] == pytest.approx([sdr_signal] * 3, abs=0.005)


def test_bt2446a_is_monotonic():
    e = np.linspace(0, 1, 64)
    nits = color._hlg_to_nits(np.stack([e, e, e], axis=-1), "bt2020")
    y = color.bt2446a(nits, 1000.0)[:, 1]
    assert np.all(np.diff(y) >= -1e-9)


def test_pq_peak():
    assert color._pq_to_nits(np.array([1.0])) == pytest.approx([10000.0], rel=1e-6)


@pytest.mark.parametrize("transfer, peak, expected", [
    ("hlg", 4000, 1000.0),
    ("pq", None, 1000.0),
    ("pq", 100, 400.0),
    ("pq", 1600, 1600.0),
    ("pq", 50000, 10000.0),
])
def test_hdr_peak_clamps(transfer, peak, expected):
    assert color.hdr_peak(transfer, peak) == expected


@pytest.mark.parametrize("prim, trc, target, expected", [
    ("bt709", "sdr", "srgb", False),
    ("bt709", "sdr", "p3", True),
    ("p3", "sdr", "p3", False),
    ("bt2020", "hlg", "hlg", False),
    ("bt2020", "hlg", "srgb", True),
    ("bt709", "sdr", "hlg", True),
])
def test_needs_lut(prim, trc, target, expected):
    assert color.needs_lut(prim, trc, target) is expected


def test_normalizers():
    assert color.normalize_primaries("bt2020") == "bt2020"
    assert color.normalize_primaries("smpte432") == "p3"
    assert color.normalize_primaries(None) == "bt709"
    assert color.normalize_transfer("arib-std-b67") == "hlg"
    assert color.normalize_transfer("smpte2084") == "pq"
    assert color.normalize_transfer("bt709") == "sdr"


def test_conversion_matrix_keeps_white():
    for src, dst in (("bt709", "p3"), ("p3", "bt2020"), ("bt2020", "bt709")):
        assert color._conv_matrix(src, dst) @ np.ones(3) == pytest.approx([1, 1, 1], abs=1e-6)


def test_sdr_cube_identity_when_no_conversion():
    lines = color.build_cube("bt709", "sdr", "srgb", size=5).splitlines()
    assert lines[0] == "LUT_3D_SIZE 5"
    vals = np.array([[float(x) for x in ln.split()] for ln in lines[1:]])
    g = np.linspace(0, 1, 5)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    expected = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    assert vals == pytest.approx(expected, abs=1e-5)


def test_lut_path_caches(isolated_workspace):
    p = color.lut_path(isolated_workspace / "cache", "bt2020", "hlg", "srgb")
    assert p.exists() and p.parent == isolated_workspace / "cache"
    mtime = p.stat().st_mtime_ns
    assert color.lut_path(isolated_workspace / "cache", "bt2020", "hlg", "srgb").stat().st_mtime_ns == mtime


def test_hex_to_srgb_is_identity():
    assert color.hex_to_target("#ff5a36", "srgb") == (0xFF, 0x5A, 0x36)
    assert color.hex_to_target("#fff", "srgb") == (255, 255, 255)


@pytest.mark.xfail(not IS_MACOS, strict=True,
                   reason="Display P3 ICC only loads from macOS ColorSync; collage/assets/DisplayP3.icc is missing, "
                          "so every P3 preview/export fails off macOS")
def test_display_p3_profile_available():
    assert color.p3_icc_bytes()[36:40] == b"acsp"
