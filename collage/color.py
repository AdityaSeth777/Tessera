"""Color management: every image is converted into a single working space
(Display P3 or sRGB) before compositing, and exports embed the matching ICC."""

import hashlib
import io
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

P3_ICC_PATHS = [
    Path(__file__).parent / "assets" / "DisplayP3.icc",
    Path("/System/Library/ColorSync/Profiles/Display P3.icc"),
]

TARGETS = ("p3", "srgb")


@lru_cache(maxsize=1)
def p3_icc_bytes() -> bytes:
    for p in P3_ICC_PATHS:
        if p.exists():
            return p.read_bytes()
    raise RuntimeError("Display P3 ICC profile not found (expected macOS ColorSync profile).")


@lru_cache(maxsize=1)
def srgb_icc_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def icc_bytes(target: str) -> bytes:
    return p3_icc_bytes() if target == "p3" else srgb_icc_bytes()


@lru_cache(maxsize=64)
def _profile(icc: bytes) -> ImageCms.ImageCmsProfile:
    return ImageCms.ImageCmsProfile(io.BytesIO(icc))


_transform_cache: dict = {}


def _transform(src_icc: bytes, dst_icc: bytes, mode_in: str, mode_out: str):
    key = (hashlib.sha1(src_icc).digest(), hashlib.sha1(dst_icc).digest(), mode_in, mode_out)
    t = _transform_cache.get(key)
    if t is None:
        t = ImageCms.buildTransform(
            _profile(src_icc), _profile(dst_icc), mode_in, mode_out,
            renderingIntent=ImageCms.Intent.PERCEPTUAL,
        )
        _transform_cache[key] = t
    return t


def source_icc(img: Image.Image) -> bytes | None:
    """ICC of the image, synthesising one from an HEIF nclx box when there is no ICC."""
    icc = img.info.get("icc_profile")
    if icc:
        return icc
    nclx = img.info.get("nclx_profile")
    if isinstance(nclx, dict) and nclx.get("color_primaries") == 12:
        return p3_icc_bytes()
    return None


def describe_space(img: Image.Image) -> str:
    icc = source_icc(img)
    if not icc:
        return "sRGB (untagged)"
    try:
        name = ImageCms.getProfileDescription(_profile(icc)).strip()
        return name or "Embedded ICC"
    except Exception:
        return "Embedded ICC"


def is_wide_gamut(img: Image.Image) -> bool:
    d = describe_space(img).lower()
    return any(k in d for k in ("p3", "2020", "adobe", "prophoto", "rec2020", "romm"))


def to_target(img: Image.Image, target: str) -> Image.Image:
    """Return an RGB/RGBA copy of img with pixel values in the target space."""
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    icc = source_icc(img)
    dst = icc_bytes(target)

    if img.mode == "CMYK" and icc:
        try:
            out = ImageCms.applyTransform(img, _transform(icc, dst, "CMYK", "RGB"))
            return out
        except Exception:
            pass

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if has_alpha else "RGB")
        if icc:
            try:
                prof_space = _profile(icc).profile.xcolor_space.strip()
            except Exception:
                prof_space = ""
            if prof_space != "RGB":
                icc = None

    src = icc or srgb_icc_bytes()
    if src == dst:
        return img.copy()
    try:
        return ImageCms.applyTransform(img, _transform(src, dst, img.mode, img.mode))
    except Exception:
        if src is srgb_icc_bytes():
            return img.copy()
        return ImageCms.applyTransform(img, _transform(srgb_icc_bytes(), dst, img.mode, img.mode))


def hex_to_target(hex_color: str, target: str) -> tuple[int, int, int]:
    """CSS colors are sRGB; convert to the working space so the background matches the preview."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    px = Image.new("RGB", (1, 1), rgb)
    return to_target(px, target).getpixel((0, 0))


# ---------------------------------------------------------------- video LUTs

_PRIMARIES = {
    "bt709": ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)),
    "p3": ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)),
    "bt2020": ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)),
}
_D65 = (0.3127, 0.3290)


def _rgb_to_xyz(prim) -> np.ndarray:
    def xyz(xy):
        x, y = xy
        return np.array([x / y, 1.0, (1 - x - y) / y])

    m = np.stack([xyz(p) for p in prim], axis=1)
    s = np.linalg.solve(m, xyz(_D65))
    return m * s


def _conv_matrix(src: str, dst: str) -> np.ndarray:
    return np.linalg.inv(_rgb_to_xyz(_PRIMARIES[dst])) @ _rgb_to_xyz(_PRIMARIES[src])


def normalize_primaries(p: str | None) -> str:
    p = (p or "").lower()
    if p in ("bt2020",):
        return "bt2020"
    if p in ("smpte432", "smpte431"):
        return "p3"
    return "bt709"


def normalize_transfer(t: str | None) -> str:
    t = (t or "").lower()
    if t == "arib-std-b67":
        return "hlg"
    if t == "smpte2084":
        return "pq"
    return "sdr"


def needs_lut(primaries: str, transfer: str, target: str) -> bool:
    if target == "hlg":
        return not (transfer == "hlg" and primaries == "bt2020")
    dst = "p3" if target == "p3" else "bt709"
    return transfer != "sdr" or primaries != dst


def _srgb_eotf(e):
    return np.where(e <= 0.04045, e / 12.92, ((e + 0.055) / 1.055) ** 2.4)


def _srgb_oetf(l):
    l = np.clip(l, 0, 1)
    return np.where(l <= 0.0031308, l * 12.92, 1.055 * np.power(l, 1 / 2.4) - 0.055)


def _hlg_to_nits(e, prim):
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    e = np.clip(e, 0, 1)
    scene = np.where(e <= 0.5, e * e / 3.0, (np.exp((e - c) / a) + b) / 12.0)
    lw = 1000.0
    coeffs = _rgb_to_xyz(_PRIMARIES[prim])[1]
    ys = np.clip(scene @ coeffs, 1e-9, None)[..., None]
    return lw * np.power(ys, 1.2 - 1.0) * scene


def _pq_to_nits(e):
    m1, m2 = 2610 / 16384, 2523 / 4096 * 128
    c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    p = np.power(np.clip(e, 0, 1), 1 / m2)
    return 10000.0 * np.power(np.maximum(p - c1, 0) / (c2 - c3 * p), 1 / m1)


def bt2446a(nits_2020, l_hdr: float, l_sdr: float = 100.0):
    """ITU-R BT.2446-1 Method A HDR->SDR tone mapping.

    Input: BT.2020 display light in cd/m². Output: BT.2020 linear light where 1.0 is SDR
    reference white (BT.1886 gamma 2.4 decoded)."""
    lum = np.array([0.2627, 0.6780, 0.0593])
    p = np.power(np.clip(nits_2020 / l_hdr, 0, 1), 1 / 2.4)
    y = p @ lum
    rho_h = 1 + 32 * (l_hdr / 10000) ** (1 / 2.4)
    yp = np.log1p((rho_h - 1) * y) / np.log(rho_h)
    yc = np.where(yp <= 0.7399, 1.0770 * yp,
                  np.where(yp < 0.9909, -1.1510 * yp ** 2 + 2.7811 * yp - 0.6302, 0.5 * yp + 0.5))
    rho_s = 1 + 32 * (l_sdr / 10000) ** (1 / 2.4)
    ysdr = (np.power(rho_s, yc) - 1) / (rho_s - 1)
    f = np.where(y > 1e-9, ysdr / (1.1 * np.maximum(y, 1e-9)), 0.0)
    cb = f * (p[..., 2] - y) / 1.8814
    cr = f * (p[..., 0] - y) / 1.4746
    yt = ysdr - np.maximum(0.1 * cr, 0)
    r = yt + 1.4746 * cr
    b = yt + 1.8814 * cb
    g = (yt - lum[0] * r - lum[2] * b) / lum[1]
    return np.power(np.clip(np.stack([r, g, b], axis=-1), 0, 1), 2.4)


def _gamut_compress(rgb, lum_coeffs):
    y = np.clip(rgb @ lum_coeffs, 0, None)[..., None]
    mn = rgb.min(axis=-1, keepdims=True)
    t = np.where(mn < 0, y / np.maximum(y - mn, 1e-9), 1.0)
    return y + np.clip(t, 0, 1) * (rgb - y)


HLG_NOMINAL_PEAK = 1000.0


def hdr_peak(transfer: str, peak: float | None) -> float:
    if transfer == "hlg":
        return HLG_NOMINAL_PEAK
    if peak and peak > 0:
        return float(min(max(peak, 400.0), 10000.0))
    return 1000.0


def build_cube(primaries: str, transfer: str, target: str, size: int = 33, peak: float | None = None) -> str:
    """3D LUT text (.cube) mapping decoded video RGB to sRGB-encoded target-space RGB."""
    dst = "p3" if target == "p3" else "bt709"
    g = np.linspace(0, 1, size)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    e = np.stack([r, gg, b], axis=-1).reshape(-1, 3)

    if transfer == "sdr":
        lin = _srgb_eotf(e) @ _conv_matrix(primaries, dst).T
    else:
        nits = _hlg_to_nits(e, primaries) if transfer == "hlg" else _pq_to_nits(e)
        if primaries != "bt2020":
            nits = nits @ _conv_matrix(primaries, "bt2020").T
        lin = bt2446a(nits, hdr_peak(transfer, peak)) @ _conv_matrix("bt2020", dst).T
    lin = _gamut_compress(lin, _rgb_to_xyz(_PRIMARIES[dst])[1])
    m = np.max(lin, axis=-1, keepdims=True)
    lin = lin / np.maximum(m, 1.0)
    out = _srgb_oetf(lin)

    lines = [f"LUT_3D_SIZE {size}"]
    lines += [f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in out]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- HLG output

SDR_WHITE_NITS = 203.0  # BT.2408 reference white for SDR content placed in HDR
HLG_PEAK = 1000.0
_LUM2020 = np.array([0.2627, 0.6780, 0.0593])


def hlg_oetf(e):
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    e = np.clip(e, 0, 1)
    return np.where(e <= 1 / 12, np.sqrt(3 * e), a * np.log(np.maximum(12 * e - b, 1e-12)) + c)


def _limit_peak(nits, peak=HLG_PEAK):
    """Soft-clips max(RGB) above 75% of peak so nothing exceeds the HLG nominal display peak."""
    m = np.max(nits, axis=-1, keepdims=True)
    k = 0.75 * peak
    soft = k + (peak - k) * (1 - np.exp(-(m - k) / (peak - k)))
    return nits * np.where(m > k, soft / np.maximum(m, 1e-9), 1.0)


def nits_to_hlg(nits_2020):
    """BT.2100 HLG inverse EOTF (Lw = 1000 cd/m², gamma 1.2): display light -> HLG signal."""
    fd = np.clip(_limit_peak(np.clip(nits_2020, 0, None)), 0, None) / HLG_PEAK
    yd = np.clip(fd @ _LUM2020, 0, 1)
    ys = np.power(yd, 1 / 1.2)
    scale = np.where(yd > 1e-12, 1 / np.maximum(np.power(np.maximum(ys, 1e-12), 0.2), 1e-12), 0.0)
    return hlg_oetf(np.clip(fd * scale[..., None], 0, 1))


def build_cube_hlg(primaries: str, transfer: str, size: int, peak: float | None = None) -> str:
    g = np.linspace(0, 1, size)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    e = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    if transfer == "sdr":
        nits = _srgb_eotf(e) * SDR_WHITE_NITS
    elif transfer == "pq":
        nits = _pq_to_nits(e)
    else:
        nits = _hlg_to_nits(e, primaries)
    nits = _gamut_compress(nits @ _conv_matrix(primaries, "bt2020").T, _LUM2020)
    out = nits_to_hlg(nits)
    lines = [f"LUT_3D_SIZE {size}"] + [f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in out]
    return "\n".join(lines) + "\n"


def p3_to_hlg16(rgb8: np.ndarray, log2_boost: np.ndarray | None) -> np.ndarray:
    """8-bit Display P3 pixels (+ optional HDR gain) -> 16-bit HLG BT.2020 signal."""
    lin = _srgb_eotf(rgb8.astype(np.float32) / 255.0).astype(np.float32)
    if log2_boost is not None:
        lin *= np.exp2(log2_boost)[..., None].astype(np.float32)
    nits = (lin * SDR_WHITE_NITS) @ _conv_matrix("p3", "bt2020").T.astype(np.float32)
    return (nits_to_hlg(nits) * 65535 + 0.5).astype(np.uint16)


def lut_path(cache_dir: Path, primaries: str, transfer: str, target: str, peak: float | None = None) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    pk = "" if transfer == "sdr" else f"_{int(hdr_peak(transfer, peak))}"
    p = cache_dir / f"lut2_{primaries}_{transfer}{pk}_{target}.cube"
    if not p.exists():
        size = 33 if transfer == "sdr" else 65
        if target == "hlg":
            p.write_text(build_cube_hlg(primaries, transfer, size, peak))
        else:
            p.write_text(build_cube(primaries, transfer, target, size, peak))
    return p
