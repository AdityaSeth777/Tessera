import io
import struct

import numpy as np
import pytest
from PIL import Image

from collage import color, gainmap, media, render
from conftest import macos_only


# ------------------------------------------------------------- Apple formula

def test_apple_gain_formula():
    boost = np.exp2(gainmap.gain_to_log2(np.array([0, 127, 255], np.float32), 4.0))
    assert boost == pytest.approx([1.0, 1.64, 4.0], abs=0.005)


def test_xmp_headroom_attribute_and_element():
    assert gainmap._xmp_headroom(b'<x HDRGainMap:HDRGainMapHeadroom="3.5"/>') == 3.5
    assert gainmap._xmp_headroom(b"<HDRGainMap:HDRGainMapHeadroom>2.25</HDRGainMap:HDRGainMapHeadroom>") == 2.25
    assert gainmap._xmp_headroom(b"nothing here") is None


def _maker_note(tag33: tuple[int, int], tag48: tuple[int, int]) -> bytes:
    """Synthetic big-endian Apple iOS maker note with two SRATIONAL tags."""
    head = b"Apple iOS\x00" + b"\x00\x01" + b"MM"
    n = 2
    data_start = len(head) + 2 + n * 12 + 4
    ifd = struct.pack(">H", n)
    ifd += struct.pack(">HHII", 33, 10, 1, data_start)
    ifd += struct.pack(">HHII", 48, 10, 1, data_start + 8)
    ifd += struct.pack(">I", 0)
    return head + ifd + struct.pack(">ii", *tag33) + struct.pack(">ii", *tag48)


@pytest.mark.parametrize("tag33, tag48, stops", [
    ((1, 2), (5, 1000), -20 * 0.005 + 1.8),
    ((1, 2), (1, 2), -0.101 * 0.5 + 1.601),
    ((2, 1), (5, 1000), -70 * 0.005 + 3.0),
    ((2, 1), (1, 2), -0.303 * 0.5 + 2.303),
])
def test_maker_note_headroom(tag33, tag48, stops):
    assert gainmap.headroom_from_maker_note(_maker_note(tag33, tag48)) == pytest.approx(2 ** stops, rel=1e-6)


def test_maker_note_rejects_other_data():
    assert gainmap.headroom_from_maker_note(b"Canon\x00" + b"\x00" * 40) is None


# ------------------------------------------------------------- Ultra HDR JPEG

def _segments(data: bytes):
    """(marker, payload_offset, payload) for every segment before SOS."""
    assert data[:2] == b"\xff\xd8"
    pos = 2
    while True:
        assert data[pos] == 0xFF
        marker = data[pos + 1]
        (length,) = struct.unpack(">H", data[pos + 2:pos + 4])
        yield marker, pos + 4, data[pos + 4:pos + 2 + length]
        if marker == 0xDA:
            return
        pos += 2 + length


def _base_and_map():
    base = Image.new("RGB", (64, 48), (200, 120, 40))
    log2map = np.zeros((24, 32), np.float32)
    log2map[6:18, 8:24] = 2.0
    return base, log2map


def test_ultrahdr_jpeg_structure(tmp_path):
    base, log2map = _base_and_map()
    out = tmp_path / "hdr.jpg"
    assert gainmap.write_ultrahdr_jpeg(base, color.srgb_icc_bytes(), log2map, out) is True
    data = out.read_bytes()

    segs = list(_segments(data))
    mpf = [(off, p) for m, off, p in segs if m == 0xE2 and p.startswith(b"MPF\x00")]
    assert len(mpf) == 1
    xmp = [p for m, _, p in segs if m == 0xE1 and p.startswith(gainmap.XMP_NS)]
    assert xmp and b"Item:Semantic=\"GainMap\"" in xmp[0]

    off, payload = mpf[0]
    tiff = off + 4
    assert data[tiff:tiff + 4] == b"MM\x00\x2a"
    (ifd_off,) = struct.unpack(">I", data[tiff + 4:tiff + 8])
    (count,) = struct.unpack(">H", data[tiff + ifd_off:tiff + ifd_off + 2])
    tags = {}
    for i in range(count):
        e = tiff + ifd_off + 2 + 12 * i
        tag, typ, n, val = struct.unpack(">HHII", data[e:e + 12])
        tags[tag] = val
    entries = tiff + tags[0xB002]
    _, size1, off1, _, _ = struct.unpack(">IIIHH", data[entries:entries + 16])
    _, size2, off2, _, _ = struct.unpack(">IIIHH", data[entries + 16:entries + 32])

    gain_start = tiff + off2
    assert off1 == 0
    assert size1 == gain_start
    assert data[gain_start:gain_start + 2] == b"\xff\xd8"
    assert size2 == len(data) - gain_start
    assert f'Item:Length="{size2}"'.encode() in xmp[0]

    gain_jpeg = data[gain_start:]
    assert b'hdrgm:GainMapMax="2.000000"' in gain_jpeg
    assert b'hdrgm:HDRCapacityMax="2.000000"' in gain_jpeg
    assert Image.open(io.BytesIO(gain_jpeg)).mode == "L"
    primary = Image.open(out)
    assert primary.size == (64, 48)
    assert primary.info.get("icc_profile") == color.srgb_icc_bytes()


def test_ultrahdr_without_boost_writes_plain_jpeg(tmp_path):
    base, _ = _base_and_map()
    out = tmp_path / "sdr.jpg"
    assert gainmap.write_ultrahdr_jpeg(base, color.srgb_icc_bytes(), np.zeros((24, 32), np.float32), out) is False
    assert b"MPF\x00" not in out.read_bytes()


# ------------------------------------------------------------- collage gain map compositing

def _register_image(mid, w, h, gain_value=None, headroom=4.0):
    d = media.media_dir(mid)
    d.mkdir(parents=True, exist_ok=True)
    meta = {"id": mid, "kind": "image", "width": w, "height": h, "aspect": w / h, "ext": ".png"}
    if gain_value is not None:
        Image.new("L", (w, h), gain_value).save(d / "gainmap.png")
        meta.update(gainmap=True, headroom=headroom)
    return meta


def _tile(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h, "fit": "cover", "zoom": 1, "panX": 0, "panY": 0}


def test_gain_log2_compositing():
    hdr = _register_image("hdr", 64, 64, gain_value=255)
    sdr = _register_image("sdr", 64, 64)
    tiles = [(_tile(0, 0, 1, 1), hdr), (_tile(0.5, 0, 0.5, 1), sdr)]
    acc = render._gain_log2(tiles, 200, 100)
    assert acc.shape == (50, 100)
    assert np.exp2(acc[25, 10]) == pytest.approx(4.0, abs=0.01)
    assert np.exp2(acc[25, 90]) == pytest.approx(1.0, abs=0.01)


def test_has_gainmap_requires_file():
    meta = _register_image("flag-only", 32, 32)
    meta.update(gainmap=True, headroom=4.0)
    assert media.has_gainmap(meta) is False
    assert media.has_gainmap(_register_image("real", 32, 32, gain_value=128)) is True


# ------------------------------------------------------------- Apple HDR HEIC (ImageIO)

@macos_only
def test_apple_heic_round_trip(tmp_path):
    if not gainmap.apple_writer_available():
        pytest.skip("PyObjC Quartz not installed")
    base = Image.new("RGB", (128, 96), (180, 140, 90))
    log2map = np.full((48, 64), 2.0, np.float32)
    out = tmp_path / "hdr.heic"
    assert gainmap.write_apple_heic(base, log2map, out) is True
    got = gainmap.read_apple_gainmap(out)
    assert got is not None
    gain, headroom = got
    assert headroom == pytest.approx(4.0, rel=0.01)
    assert np.asarray(gain).mean() > 240
