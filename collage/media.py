import json
import shutil
import subprocess
import threading
import time
import uuid
from fractions import Fraction
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

from . import color

pillow_heif.register_heif_opener()
Image.MAX_IMAGE_PIXELS = 400_000_000

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT / "workspace"
MEDIA_DIR = WORKSPACE / "media"
CACHE_DIR = WORKSPACE / "cache"
INDEX = MEDIA_DIR / "index.json"

VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}
IMAGE_EXTS = {".heic", ".heif", ".hif", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff",
              ".gif", ".bmp", ".avif"}

PREVIEW_EDGE = 2048
THUMB_EDGE = 400
PROXY_EDGE = 1280

_lock = threading.Lock()


def _ensure_dirs():
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_index() -> dict:
    _ensure_dirs()
    if INDEX.exists():
        return json.loads(INDEX.read_text())
    return {}


def _save_index(idx: dict):
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, indent=1))
    tmp.replace(INDEX)


def get(media_id: str) -> dict | None:
    return load_index().get(media_id)


def media_dir(media_id: str) -> Path:
    return MEDIA_DIR / media_id


def source_path(meta: dict) -> Path:
    return media_dir(meta["id"]) / f"source{meta['ext']}"


def open_image(path: Path) -> Image.Image:
    img = Image.open(path)
    icc = img.info.get("icc_profile")
    nclx = img.info.get("nclx_profile")
    img = ImageOps.exif_transpose(img)
    if icc:
        img.info["icc_profile"] = icc
    if nclx:
        img.info["nclx_profile"] = nclx
    return img


def _has_gainmap(path: Path) -> bool:
    try:
        heif = pillow_heif.open_heif(path, convert_hdr_to_8bit=True)
        aux = heif.info.get("aux") or {}
        return any("gainmap" in str(k).lower() for k in aux)
    except Exception:
        return False


def _save_preview(img: Image.Image, out: Path, edge: int, quality: int):
    im = color.to_target(img, "p3")
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    im.thumbnail((edge, edge), Image.LANCZOS)
    im.save(out, "JPEG", quality=quality, icc_profile=color.p3_icc_bytes(), optimize=True)


# ------------------------------------------------------------------ video

def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"])))
    rot = (stream.get("tags") or {}).get("rotate")
    return int(rot) if rot else 0


def probe_video(path: Path) -> dict:
    info = ffprobe(path)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if v is None:
        raise ValueError("No video stream found")
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    w, h = int(v["width"]), int(v["height"])
    sar = v.get("sample_aspect_ratio")
    if sar and sar not in ("0:1", "1:1", "N/A"):
        n, d = (int(x) for x in sar.split(":"))
        if n and d:
            w = round(w * n / d)
    rot = _rotation(v)
    if abs(rot) % 180 == 90:
        w, h = h, w
    duration = float(v.get("duration") or info["format"].get("duration") or 0)
    try:
        fps = float(Fraction(v.get("avg_frame_rate") or "30/1"))
    except (ZeroDivisionError, ValueError):
        fps = 30.0
    matrix = (v.get("color_space") or "").lower()
    raw_prim = v.get("color_primaries") or ("bt2020" if matrix.startswith("bt2020") else None)
    prim = color.normalize_primaries(raw_prim)
    trc = color.normalize_transfer(v.get("color_transfer"))
    if matrix not in ("bt709", "bt2020nc", "bt2020c", "smpte170m", "bt470bg"):
        matrix = "bt2020" if prim == "bt2020" else "bt709"
    elif matrix.startswith("bt2020"):
        matrix = "bt2020"
    return {
        "width": w, "height": h, "duration": duration, "fps": fps or 30.0,
        "hasAudio": a is not None, "primaries": prim, "transfer": trc,
        "matrix": matrix, "range": "pc" if v.get("color_range") == "pc" else "tv",
        "codec": v.get("codec_name"),
    }


def video_rgb_filters(meta: dict, target: str) -> str:
    """Filter chain: decoded video -> 16-bit RGB in the target working space."""
    chain = [f"scale=in_color_matrix={meta['matrix']}:in_range={meta['range']}", "format=rgb48le"]
    if color.needs_lut(meta["primaries"], meta["transfer"], target):
        lut = color.lut_path(CACHE_DIR, meta["primaries"], meta["transfer"], target)
        chain.append(f"lut3d=file='{lut}':interp=tetrahedral")
    return ",".join(chain)


def extract_frame(meta: dict, t: float, target: str) -> Image.Image:
    """Grab one frame as an RGB PIL image whose pixels are in the target space."""
    t = max(0.0, min(t, max(meta["duration"] - 0.05, 0)))
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(source_path(meta)),
        "-frames:v", "1", "-vf", video_rgb_filters(meta, target) + ",format=rgb24",
        "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    data = subprocess.run(cmd, capture_output=True, check=True).stdout
    from io import BytesIO
    img = Image.open(BytesIO(data))
    img.load()
    img.info["icc_profile"] = color.icc_bytes(target)
    return img


def _make_proxy(meta: dict, out: Path):
    w, h = meta["width"], meta["height"]
    scale = min(1.0, PROXY_EDGE / max(w, h))
    pw, ph = max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
    vf = (video_rgb_filters(meta, "srgb")
          + f",scale={pw}:{ph}:flags=bicubic:out_color_matrix=bt709:out_range=tv,format=yuv420p")
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-i", str(source_path(meta)), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-movflags", "+faststart",
    ]
    cmd += ["-c:a", "aac", "-b:a", "128k"] if meta["hasAudio"] else ["-an"]
    subprocess.run(cmd + [str(out)], check=True, capture_output=True)


# ------------------------------------------------------------------ ingest

def ingest(upload_path: Path, original_name: str) -> dict:
    _ensure_dirs()
    ext = Path(original_name).suffix.lower() or ".bin"
    mid = uuid.uuid4().hex[:12]
    d = media_dir(mid)
    d.mkdir(parents=True)
    src = d / f"source{ext}"
    shutil.move(str(upload_path), src)
    meta = {"id": mid, "name": original_name, "ext": ext, "added": time.time()}
    try:
        if ext in VIDEO_EXTS:
            meta.update(kind="video", **probe_video(src))
            frame = extract_frame(meta, min(0.5, meta["duration"] / 2), "p3")
            _save_preview(frame, d / "preview.jpg", PREVIEW_EDGE, 88)
            _save_preview(frame, d / "thumb.jpg", THUMB_EDGE, 80)
            _make_proxy(meta, d / "proxy.mp4")
            meta["space"] = {"hlg": "HDR (HLG)", "pq": "HDR (PQ)"}.get(meta["transfer"]) or (
                "Display P3" if meta["primaries"] == "p3" else
                "BT.2020" if meta["primaries"] == "bt2020" else "BT.709")
            meta["wide"] = meta["primaries"] != "bt709" or meta["transfer"] != "sdr"
        else:
            img = open_image(src)
            meta.update(kind="image", width=img.width, height=img.height,
                        space=color.describe_space(img), wide=color.is_wide_gamut(img),
                        gainmap=ext in (".heic", ".heif", ".hif") and _has_gainmap(src))
            _save_preview(img, d / "preview.jpg", PREVIEW_EDGE, 90)
            _save_preview(img, d / "thumb.jpg", THUMB_EDGE, 82)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    meta["aspect"] = meta["width"] / meta["height"]
    with _lock:
        idx = load_index()
        idx[mid] = meta
        _save_index(idx)
    return meta


def delete(media_id: str):
    with _lock:
        idx = load_index()
        if idx.pop(media_id, None) is not None:
            _save_index(idx)
    shutil.rmtree(media_dir(media_id), ignore_errors=True)


def public(meta: dict) -> dict:
    base = f"/m/{meta['id']}"
    out = {k: meta[k] for k in ("id", "kind", "name", "ext", "width", "height", "aspect")}
    out.update(space=meta.get("space"), wide=meta.get("wide", False),
               gainmap=meta.get("gainmap", False),
               preview=f"{base}/preview.jpg", thumb=f"{base}/thumb.jpg")
    if meta["kind"] == "video":
        out.update(duration=meta["duration"], hasAudio=meta["hasAudio"], fps=meta["fps"],
                   proxy=f"{base}/proxy.mp4")
    return out
