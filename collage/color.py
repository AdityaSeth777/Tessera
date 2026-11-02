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


def _tonemap(x):
    """Identity below the knee, smooth exponential shoulder to 1.0 above it."""
    k = 0.6
    return np.where(x <= k, x, k + (1 - k) * (1 - np.exp(-(x - k) / (1 - k))))


def _gamut_compress(rgb, lum_coeffs):
    y = np.clip(rgb @ lum_coeffs, 0, None)[..., None]
    mn = rgb.min(axis=-1, keepdims=True)
    t = np.where(mn < 0, y / np.maximum(y - mn, 1e-9), 1.0)
    return y + np.clip(t, 0, 1) * (rgb - y)


def build_cube(primaries: str, transfer: str, target: str, size: int = 33) -> str:
    """3D LUT text (.cube) mapping decoded video RGB to sRGB-encoded target-space RGB."""
    dst = "p3" if target == "p3" else "bt709"
    g = np.linspace(0, 1, size)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    e = np.stack([r, gg, b], axis=-1).reshape(-1, 3)

    if transfer == "sdr":
        lin = _srgb_eotf(e)
    else:
        nits = _hlg_to_nits(e, primaries) if transfer == "hlg" else _pq_to_nits(e)
        x = nits / 203.0 * 0.9
        m = np.max(x, axis=-1, keepdims=True)
        lin = x * (_tonemap(m) / np.maximum(m, 1e-9))

    lin = lin @ _conv_matrix(primaries, dst).T
    lin = _gamut_compress(lin, _rgb_to_xyz(_PRIMARIES[dst])[1])
    m = np.max(lin, axis=-1, keepdims=True)
    lin = lin / np.maximum(m, 1.0)
    out = _srgb_oetf(lin)

    lines = [f"LUT_3D_SIZE {size}"]
    lines += [f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in out]
    return "\n".join(lines) + "\n"


def lut_path(cache_dir: Path, primaries: str, transfer: str, target: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / f"lut_{primaries}_{transfer}_{target}.cube"
    if not p.exists():
        size = 33 if transfer == "sdr" else 49
        p.write_text(build_cube(primaries, transfer, target, size))
    return p
