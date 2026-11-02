"""HDR gain maps.

Apple HEIC gain map (iPhone, aux image "urn:com:apple:photo:2020:aux:hdrgainmap"):
    HDR_linear = SDR_linear * (1 + (headroom - 1) * srgb_eotf(gain))
We composite gains as log2(boost) so tiles with different headrooms blend correctly, then
write either an Ultra HDR JPEG (Adobe/Google hdrgm XMP + MPF secondary image) or, on
macOS, an Apple-style HEIC through ImageIO.
"""

import io
import re
import struct
from pathlib import Path

import numpy as np
import pillow_heif
from PIL import Image, ImageOps

from . import color

APPLE_AUX = "urn:com:apple:photo:2020:aux:hdrgainmap"
DEFAULT_HEADROOM = 4.0


srgb_eotf = color._srgb_eotf
srgb_oetf = color._srgb_oetf


# ------------------------------------------------------------------ reading

def _xmp_headroom(raw: bytes) -> float | None:
    m = re.search(rb"HDRGainMapHeadroom\s*(?:=\s*[\"']|>)\s*([0-9]*\.?[0-9]+)", raw)
    return float(m.group(1)) if m else None


def _maker_note_values(maker: bytes) -> dict:
    """Parses tags out of an Apple iOS maker note (big-endian IFD, offsets relative to the note)."""
    if not maker.startswith(b"Apple iOS\x00") or len(maker) < 16:
        return {}
    bo = maker[12:14]
    fmt = ">" if bo == b"MM" else "<"
    pos = 14
    (count,) = struct.unpack_from(fmt + "H", maker, pos)
    pos += 2
    sizes = {1: 1, 3: 2, 4: 4, 5: 8, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
    out = {}
    for _ in range(count):
        if pos + 12 > len(maker):
            break
        tag, typ, n = struct.unpack_from(fmt + "HHI", maker, pos)
        size = sizes.get(typ, 0) * n
        data_off = pos + 8 if size <= 4 else struct.unpack_from(fmt + "I", maker, pos + 8)[0]
        pos += 12
        if n != 1 or not size or data_off + size > len(maker):
            continue
        if typ in (5, 10):
            a, b = struct.unpack_from(fmt + ("II" if typ == 5 else "ii"), maker, data_off)
            out[tag] = a / b if b else 0.0
        elif typ == 11:
            out[tag] = struct.unpack_from(fmt + "f", maker, data_off)[0]
        elif typ == 12:
            out[tag] = struct.unpack_from(fmt + "d", maker, data_off)[0]
        elif typ in (3, 4, 8, 9):
            out[tag] = struct.unpack_from(fmt + {3: "H", 4: "I", 8: "h", 9: "i"}[typ], maker, data_off)[0]
    return out


def headroom_from_maker_note(maker: bytes) -> float | None:
    """Apple's documented formula using maker note tags 33 (0x21) and 48 (0x30)."""
    v = _maker_note_values(maker)
    if 33 not in v or 48 not in v:
        return None
    m33, m48 = v[33], v[48]
    if m33 < 1.0:
        stops = -20.0 * m48 + 1.8 if m48 <= 0.01 else -0.101 * m48 + 1.601
    else:
        stops = -70.0 * m48 + 3.0 if m48 <= 0.01 else -0.303 * m48 + 2.303
    return float(2.0 ** max(stops, 0.0))


def _maker_note(img: Image.Image) -> bytes | None:
    try:
        mk = img.getexif().get_ifd(0x8769).get(0x927C)
        return bytes(mk) if mk else None
    except Exception:
        return None


def _best_orientation(gain: Image.Image, base: Image.Image) -> Image.Image | None:
    """Gain maps may be stored unrotated; pick the orientation whose shape and content match the photo."""
    pa = base.width / base.height
    T = Image.Transpose
    cands = []
    if abs(gain.width / gain.height - pa) / pa < 0.03:
        cands += [gain, gain.transpose(T.ROTATE_180)]
    if abs(gain.height / gain.width - pa) / pa < 0.03:
        cands += [gain.transpose(T.ROTATE_90), gain.transpose(T.ROTATE_270)]
    if not cands:
        return None
    ref = np.asarray(base.convert("L").resize((64, 64)), dtype=float).ravel()
    ref -= ref.mean()

    def corr(g):
        a = np.asarray(g.resize((64, 64)), dtype=float).ravel()
        a -= a.mean()
        return float(a @ ref / (np.linalg.norm(a) * np.linalg.norm(ref) + 1e-9))

    return max(cands, key=corr)


def read_apple_gainmap(path: Path) -> tuple[Image.Image, float] | None:
    """Returns (gain map as 'L', aligned to the EXIF-transposed photo, headroom) or None.

    Any failure means "no HDR": the photo still imports as SDR."""
    try:
        return _read_apple_gainmap(path)
    except Exception:
        return None


def _read_apple_gainmap(path: Path) -> tuple[Image.Image, float] | None:
    try:
        heif = pillow_heif.open_heif(path, convert_hdr_to_8bit=True)
        ids = (heif.info.get("aux") or {}).get(APPLE_AUX)
        if not ids:
            return None
        gain = heif.get_aux_image(ids[0]).to_pillow().convert("L")
    except Exception:
        return None
    base = Image.open(path)
    gain = _best_orientation(gain, base)
    if gain is None:
        return None
    exif = base.info.get("exif")
    if exif:
        gain.info["exif"] = exif
        gain = ImageOps.exif_transpose(gain)
    headroom = _xmp_headroom(Path(path).read_bytes())
    if not headroom:
        mk = _maker_note(base)
        headroom = headroom_from_maker_note(mk) if mk else None
    return gain, float(headroom or DEFAULT_HEADROOM)


def gain_to_log2(gain_l: np.ndarray, headroom: float) -> np.ndarray:
    g = srgb_eotf(gain_l.astype(np.float32) / 255.0)
    return np.log2(1.0 + (headroom - 1.0) * g)


# ------------------------------------------------------------------ writing: Ultra HDR JPEG

def _app(marker: int, payload: bytes) -> bytes:
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("JPEG segment too large")
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


XMP_NS = b"http://ns.adobe.com/xap/1.0/\x00"


def _xmp_gainmap(max_log2: float) -> bytes:
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="local-collage">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" '
        f'hdrgm:Version="1.0" hdrgm:GainMapMin="0" hdrgm:GainMapMax="{max_log2:.6f}" hdrgm:Gamma="1" '
        'hdrgm:OffsetSDR="0" hdrgm:OffsetHDR="0" hdrgm:HDRCapacityMin="0" '
        f'hdrgm:HDRCapacityMax="{max_log2:.6f}" hdrgm:BaseRenditionIsHDR="False"/>'
        '</rdf:RDF></x:xmpmeta>'
    ).encode()


def _xmp_primary(gain_len: int) -> bytes:
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="local-collage">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:Container="http://ns.google.com/photos/1.0/container/" '
        'xmlns:Item="http://ns.google.com/photos/1.0/container/item/" '
        'xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" hdrgm:Version="1.0">'
        '<Container:Directory><rdf:Seq>'
        '<rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="Primary" Item:Mime="image/jpeg"/></rdf:li>'
        f'<rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="GainMap" Item:Mime="image/jpeg" Item:Length="{gain_len}"/></rdf:li>'
        '</rdf:Seq></Container:Directory>'
        '</rdf:Description></rdf:RDF></x:xmpmeta>'
    ).encode()


def _mpf(primary_size: int, gain_size: int, gain_offset: int) -> bytes:
    """MPF APP2 payload. Offsets are relative to the start of the MPF TIFF header."""
    entries_off = 8 + 2 + 3 * 12 + 4
    ifd = struct.pack(">H", 3)
    ifd += struct.pack(">HHI4s", 0xB000, 7, 4, b"0100")
    ifd += struct.pack(">HHII", 0xB001, 4, 1, 2)
    ifd += struct.pack(">HHII", 0xB002, 7, 32, entries_off)
    ifd += struct.pack(">I", 0)
    entries = struct.pack(">IIIHH", 0x030000, primary_size, 0, 0, 0)
    entries += struct.pack(">IIIHH", 0x000000, gain_size, gain_offset, 0, 0)
    return b"MPF\x00" + b"MM\x00\x2a" + struct.pack(">I", 8) + ifd + entries


def _jpeg_bytes(img: Image.Image, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", **kw)
    return buf.getvalue()


def write_ultrahdr_jpeg(base: Image.Image, icc: bytes, log2map: np.ndarray, out: Path, quality: int = 92):
    max_log2 = float(log2map.max())
    base_jpeg = _jpeg_bytes(base, quality=quality, subsampling=0 if quality >= 90 else 2,
                            icc_profile=icc, optimize=True)
    if max_log2 < 0.02:
        out.write_bytes(base_jpeg)
        return False
    enc = np.clip(log2map / max_log2, 0, 1)
    gain_img = Image.fromarray((enc * 255 + 0.5).astype(np.uint8), "L")
    gain_jpeg = _jpeg_bytes(gain_img, quality=90)
    gain_jpeg = gain_jpeg[:2] + _app(0xE1, XMP_NS + _xmp_gainmap(max_log2)) + gain_jpeg[2:]

    app1 = _app(0xE1, XMP_NS + _xmp_primary(len(gain_jpeg)))
    mpf_len = len(_app(0xE2, _mpf(0, 0, 0)))
    rest = base_jpeg[2:]
    primary_size = 2 + len(app1) + mpf_len + len(rest)
    tiff_start = 2 + len(app1) + 4 + 4
    app2 = _app(0xE2, _mpf(primary_size, len(gain_jpeg), primary_size - tiff_start))
    out.write_bytes(base_jpeg[:2] + app1 + app2 + rest + gain_jpeg)
    return True


# ------------------------------------------------------------------ writing: Apple HEIC via ImageIO

def apple_writer_available() -> bool:
    try:
        import Quartz  # noqa: F401
        return True
    except Exception:
        return False


def write_apple_heic(base: Image.Image, log2map: np.ndarray, out: Path, quality: int = 92) -> bool:
    import Quartz
    from Foundation import NSData, NSURL

    rgb = np.asarray(base.convert("RGB"))
    h, w = rgb.shape[:2]
    rgba = np.ascontiguousarray(np.dstack([rgb, np.full((h, w), 255, np.uint8)]))
    p3 = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceDisplayP3)
    prov = Quartz.CGDataProviderCreateWithData(None, rgba.tobytes(), rgba.nbytes, None)
    cg = Quartz.CGImageCreate(w, h, 8, 32, w * 4, p3, Quartz.kCGImageAlphaNoneSkipLast, prov, None, False, 0)

    dest = Quartz.CGImageDestinationCreateWithURL(NSURL.fileURLWithPath_(str(out)), "public.heic", 1, None)
    Quartz.CGImageDestinationAddImage(dest, cg, {Quartz.kCGImageDestinationLossyCompressionQuality: quality / 100})
    max_log2 = float(log2map.max())
    has_hdr = max_log2 >= 0.02
    if has_hdr:
        headroom = 2.0 ** max_log2
        g = srgb_oetf((np.exp2(log2map) - 1.0) / (headroom - 1.0))
        gm = np.ascontiguousarray((g * 255 + 0.5).astype(np.uint8))
        gh, gw = gm.shape
        meta = Quartz.CGImageMetadataCreateMutable()
        Quartz.CGImageMetadataRegisterNamespaceForPrefix(meta, "http://ns.apple.com/HDRGainMap/1.0/", "HDRGainMap", None)
        Quartz.CGImageMetadataSetValueWithPath(meta, None, "HDRGainMap:HDRGainMapVersion", 131072)
        Quartz.CGImageMetadataSetValueWithPath(meta, None, "HDRGainMap:HDRGainMapHeadroom", float(headroom))
        info = {
            Quartz.kCGImageAuxiliaryDataInfoData: NSData.dataWithBytes_length_(gm.tobytes(), gm.nbytes),
            Quartz.kCGImageAuxiliaryDataInfoDataDescription: {
                "Width": gw, "Height": gh, "BytesPerRow": gw, "PixelFormat": 1278226488,  # 'L008'
            },
            Quartz.kCGImageAuxiliaryDataInfoMetadata: meta,
        }
        Quartz.CGImageDestinationAddAuxiliaryDataInfo(dest, Quartz.kCGImageAuxiliaryDataTypeHDRGainMap, info)
    if not Quartz.CGImageDestinationFinalize(dest):
        raise RuntimeError("ImageIO could not write the HEIC file")
    return has_hdr
