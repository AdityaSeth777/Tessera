"""Renders a resolved collage (tiles already laid out by the editor, in normalized
canvas coordinates) to a still image or a video. Placement math mirrors
static/js/layout.js `placement()` exactly so the export matches the preview."""

import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pillow_heif
from PIL import Image, ImageDraw

from . import color, gainmap, media

EXPORT_DIR = media.ROOT / "exports"

STILL_FORMATS = {
    "jpeg-srgb": ("srgb", "JPEG", ".jpg"),
    "jpeg-p3": ("p3", "JPEG", ".jpg"),
    "png-srgb": ("srgb", "PNG", ".png"),
    "png-p3": ("p3", "PNG", ".png"),
    "tiff-p3": ("p3", "TIFF", ".tif"),
    "heic-p3": ("p3", "HEIF", ".heic"),
    "jpeg-p3-hdr": ("p3", "ULTRAHDR", ".jpg"),
    "heic-p3-hdr": ("p3", "APPLEHDR", ".heic"),
}
VIDEO_FORMATS = {
    "mp4-h264": ("srgb", ".mp4"),
    "mov-hevc-p3": ("p3", ".mov"),
    "mov-hevc-hlg": ("hlg", ".mov"),
}


def canvas_size(aspect, long_edge: int) -> tuple[int, int]:
    aw, ah = float(aspect[0]), float(aspect[1])
    if aw >= ah:
        w, h = long_edge, long_edge * ah / aw
    else:
        w, h = long_edge * aw / ah, long_edge
    return max(2, int(round(w / 2)) * 2), max(2, int(round(h / 2)) * 2)


def placement(tw, th, mw, mh, fit, zoom, pan_x, pan_y):
    base = max(tw / mw, th / mh) if fit == "cover" else min(tw / mw, th / mh)
    s = base * zoom
    dw, dh = mw * s, mh * s
    max_x, max_y = abs(dw - tw) / 2, abs(dh - th) / 2
    ox = max(-1.0, min(1.0, pan_x)) * max_x
    oy = max(-1.0, min(1.0, pan_y)) * max_y
    return (tw - dw) / 2 + ox, (th - dh) / 2 + oy, dw, dh, s


def tile_box(t, W, H):
    x0, y0 = round(t["x"] * W), round(t["y"] * H)
    x1, y1 = round((t["x"] + t["w"]) * W), round((t["y"] + t["h"]) * H)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def visible_region(t, tw, th, mw, mh):
    """Portion of the media visible in the tile: source crop box + destination rect."""
    left, top, dw, dh, s = placement(tw, th, mw, mh, t.get("fit", "cover"),
                                     t.get("zoom", 1), t.get("panX", 0), t.get("panY", 0))
    ix0, iy0 = max(0.0, left), max(0.0, top)
    ix1, iy1 = min(float(tw), left + dw), min(float(th), top + dh)
    if ix1 - ix0 < 1 or iy1 - iy0 < 1:
        return None
    src = ((ix0 - left) / s, (iy0 - top) / s, (ix1 - left) / s, (iy1 - top) / s)
    dx0, dy0 = int(round(ix0)), int(round(iy0))
    dst = (dx0, dy0, max(1, int(round(ix1)) - dx0), max(1, int(round(iy1)) - dy0))
    return src, dst


def rounded_mask(w: int, h: int, r: int) -> Image.Image:
    mask = Image.new("L", (w, h), 255)
    r = int(min(r, w // 2, h // 2))
    if r < 1:
        return mask
    ss = 4
    circ = Image.new("L", (2 * r * ss, 2 * r * ss), 0)
    ImageDraw.Draw(circ).ellipse((0, 0, 2 * r * ss - 1, 2 * r * ss - 1), fill=255)
    circ = circ.resize((2 * r, 2 * r), Image.LANCZOS)
    for cx, cy in ((0, 0), (w - r, 0), (0, h - r), (w - r, h - r)):
        qx, qy = (0 if cx == 0 else r), (0 if cy == 0 else r)
        mask.paste(circ.crop((qx, qy, qx + r, qy + r)), (cx, cy))
    return mask


class MediaCache:
    def __init__(self, target: str):
        self.target = target
        self._images: dict = {}

    def image(self, meta: dict, t: float = 0.0) -> Image.Image:
        key = (meta["id"], round(t, 3) if meta["kind"] == "video" else 0)
        img = self._images.get(key)
        if img is None:
            if meta["kind"] == "video":
                img = media.extract_frame(meta, t, self.target)
            else:
                img = media.open_image(media.source_path(meta))
            img = color.to_target(img, self.target)
            self._images[key] = img
        return img


def clip_time(meta: dict, start: float, t: float) -> float:
    """Where a looping clip is at time t: it plays start..end, then loops back to start."""
    seg = meta["duration"] - start
    if seg <= 0:
        return max(0.0, start)
    return start + t % seg


def tile_radius(t, W, H) -> int:
    return int(round(float(t.get("radius", 0)) * min(W, H)))


def compose_tile(src: Image.Image, t: dict, W: int, H: int):
    x0, y0, tw, th = tile_box(t, W, H)
    radius_px = tile_radius(t, W, H)
    tile = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    vis = visible_region(t, tw, th, src.width, src.height)
    if vis:
        box, (dx, dy, dw, dh) = vis
        piece = src.resize((dw, dh), Image.LANCZOS, box=box, reducing_gap=3.0)
        tile.paste(piece.convert("RGBA"), (dx, dy))
    if radius_px > 0:
        a = tile.getchannel("A")
        tile.putalpha(Image.composite(a, Image.new("L", a.size, 0), rounded_mask(tw, th, radius_px)))
    rot = float(t.get("rotation") or 0)
    cx, cy = x0 + tw / 2, y0 + th / 2
    if abs(rot) > 0.01:
        pad = 2
        padded = Image.new("RGBA", (tw + 2 * pad, th + 2 * pad), (0, 0, 0, 0))
        padded.paste(tile, (pad, pad))
        tile = padded.rotate(-rot, resample=Image.BICUBIC, expand=True)
    return tile, (int(round(cx - tile.width / 2)), int(round(cy - tile.height / 2)))


def _small_paste(canvas: Image.Image, tile: Image.Image, pos):
    x, y = pos
    l, tp = max(0, x), max(0, y)
    r, b = min(canvas.width, x + tile.width), min(canvas.height, y + tile.height)
    if r <= l or b <= tp:
        return
    region = tile.crop((l - x, tp - y, r - x, b - y))
    canvas.alpha_composite(region, (l, tp))


def _resolve(payload):
    metas = media.load_index()
    tiles = []
    for t in payload["tiles"]:
        m = metas.get(t["mediaId"])
        if m:
            tiles.append((t, m))
    return tiles


def render_still(payload: dict, target: str, long_edge: int, progress=lambda p: None) -> Image.Image:
    W, H = canvas_size(payload["canvas"]["aspect"], long_edge)
    canvas = Image.new("RGBA", (W, H), color.hex_to_target(payload["canvas"].get("bg", "#ffffff"), target) + (255,))
    cache = MediaCache(target)
    tiles = _resolve(payload)
    now = float(payload.get("time", 0))
    for i, (t, m) in enumerate(tiles):
        tt = clip_time(m, float(t.get("start", 0)), now) if m["kind"] == "video" else 0.0
        tile, pos = compose_tile(cache.image(m, tt), t, W, H)
        _small_paste(canvas, tile, pos)
        progress((i + 1) / max(1, len(tiles)))
    return canvas.convert("RGB")


def _gain_source(meta: dict, cache: dict):
    """Gain map image for a tile's media, or a tiny black stand-in with the same aspect."""
    if meta.get("gainmap") and meta["kind"] == "image":
        g = cache.get(meta["id"])
        if g is None:
            g = Image.open(media.media_dir(meta["id"]) / "gainmap.png").convert("RGB")
            cache[meta["id"]] = g
        return g, float(meta.get("headroom") or gainmap.DEFAULT_HEADROOM)
    w = max(1, meta["width"] // 16)
    return Image.new("RGB", (w, max(1, round(w / meta["aspect"])))), None


def render_gain_log2(payload: dict, W: int, H: int) -> np.ndarray:
    """Collage-wide log2(HDR boost) at half resolution, composited in tile z-order."""
    return _gain_log2(_resolve(payload), W, H)


def _gain_log2(tiles, W: int, H: int) -> np.ndarray:
    Wg, Hg = max(2, W // 2), max(2, H // 2)
    acc = np.zeros((Hg, Wg), np.float32)
    cache: dict = {}
    for t, m in tiles:
        src, headroom = _gain_source(m, cache)
        tile, (x, y) = compose_tile(src, t, Wg, Hg)
        l, tp = max(0, x), max(0, y)
        r, b = min(Wg, x + tile.width), min(Hg, y + tile.height)
        if r <= l or b <= tp:
            continue
        arr = np.asarray(tile.crop((l - x, tp - y, r - x, b - y)), np.float32)
        a = arr[..., 3] / 255.0
        val = gainmap.gain_to_log2(arr[..., 0], headroom) if headroom else 0.0
        acc[tp:b, l:r] = acc[tp:b, l:r] * (1 - a) + val * a
    return acc


def export_still(payload: dict, fmt: str, long_edge: int, quality: int, out: Path, progress) -> dict:
    target, kind, _ = STILL_FORMATS[fmt]
    img = render_still(payload, target, long_edge, lambda p: progress(p * 0.75))
    info = {"width": img.width, "height": img.height}
    if kind in ("ULTRAHDR", "APPLEHDR"):
        log2map = render_gain_log2(payload, img.width, img.height)
        progress(0.85)
        if kind == "ULTRAHDR":
            info["hdr"] = gainmap.write_ultrahdr_jpeg(img, color.p3_icc_bytes(), log2map, out, quality)
        else:
            info["hdr"] = gainmap.write_apple_heic(img, log2map, out, quality)
        info["headroom"] = round(float(2 ** log2map.max()), 3)
    else:
        save_still(img, fmt, out, quality)
    return info


def save_still(img: Image.Image, fmt: str, out: Path, quality: int):
    target, pil_fmt, _ = STILL_FORMATS[fmt]
    icc = color.icc_bytes(target)
    if pil_fmt == "JPEG":
        img.save(out, "JPEG", quality=quality, subsampling=0 if quality >= 90 else 2,
                 icc_profile=icc, optimize=True)
    elif pil_fmt == "PNG":
        img.save(out, "PNG", icc_profile=icc, compress_level=6)
    elif pil_fmt == "TIFF":
        img.save(out, "TIFF", icc_profile=icc, compression="tiff_lzw")
    else:
        heif = pillow_heif.from_pillow(img)
        heif.save(out, quality=quality, icc_profile=icc, chroma=444 if quality >= 90 else 420)


# ------------------------------------------------------------------ video

def video_duration(tiles, mode: str, custom: float) -> float:
    lens = [max(0.1, m["duration"] - float(t.get("start", 0))) for t, m in tiles if m["kind"] == "video"]
    if mode == "custom" and custom > 0:
        return custom
    if not lens:
        return custom or 5.0
    return min(lens) if mode == "shortest" else max(lens)


def _clip_mask(tw, th, radius_px, vis_rect) -> Image.Image:
    """Rounded-corner mask that is also transparent outside the visible media (letterbox)."""
    mask = rounded_mask(tw, th, radius_px)
    dx, dy, dw, dh = vis_rect
    if (dx, dy, dw, dh) != (0, 0, tw, th):
        keep = Image.new("L", (tw, th), 0)
        keep.paste(255, (dx, dy, dx + dw, dy + dh))
        mask = Image.composite(mask, keep, keep)
    return mask


def _render_clip_segment(m, t, vis, tile_size, radius_px, start, fps, target, want_audio, out, log_path):
    """Pre-renders one clip's start..end segment, fully processed for its tile, as lossless FFV1.

    Looping this file with -stream_loop restarts exactly at the tile's start point, and the
    seek is frame-accurate because the segment is re-encoded rather than stream-copied."""
    hdr = target == "hlg"
    rgba, planar = ("rgba64le", "gbrap16le") if hdr else ("rgba", "gbrap")
    tw, th = tile_size
    (sx0, sy0, sx1, sy1), (dx, dy, dw, dh) = vis
    cw, ch = max(1, int(round(sx1 - sx0))), max(1, int(round(sy1 - sy0)))
    seg_len = max(1.0 / fps, m["duration"] - start)
    chain = [
        "[0:v]setpts=PTS-STARTPTS", f"fps={fps}", media.video_rgb_filters(m, target),
        f"crop={cw}:{ch}:{int(round(sx0))}:{int(round(sy0))}",
        f"scale={dw}:{dh}:flags=lanczos", "setsar=1", f"format={rgba}",
    ]
    if (dw, dh) != (tw, th):
        chain.append(f"pad={tw}:{th}:{dx}:{dy}:color=0x00000000")
    inputs = ["-ss", f"{start:.3f}", "-i", str(media.source_path(m))]
    graph = []
    if radius_px > 0:
        mp = out.with_suffix(".mask.png")
        _clip_mask(tw, th, radius_px, (dx, dy, dw, dh)).save(mp)
        inputs += ["-loop", "1", "-framerate", str(fps), "-i", str(mp)]
        graph.append(",".join(chain) + f",format={'gbrp16le' if hdr else 'gbrp'}[c]")
        graph.append(f"[1:v]setsar=1,format={'gray16le' if hdr else 'gray'}[m]")
        graph.append(f"[c][m]mergeplanes=format={planar}:map0s=0:map0p=0:map1s=0:map1p=1:map2s=0:map2p=2:map3s=1:map3p=0[a]")
        chain = [f"[a]format={rgba}"]
    rot = float(t.get("rotation") or 0)
    if abs(rot) > 0.01:
        a = rot * math.pi / 180
        chain.append(f"rotate={a:.6f}:ow=rotw({a:.6f}):oh=roth({a:.6f}):c=none:bilinear=1")
    graph.append(",".join(chain) + f",format={planar if hdr else 'bgra'}[v]")
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph), "-map", "[v]"]
    cmd += ["-map", "0:a:0", "-c:a", "pcm_s16le", "-ar", "48000"] if want_audio else ["-an"]
    cmd += ["-t", f"{seg_len:.4f}", "-c:v", "ffv1", "-level", "3", "-pix_fmt", planar if hdr else "bgra", str(out)]
    with open(log_path, "a") as log:
        log.write(" ".join(cmd) + "\n\n")
        log.flush()
        if subprocess.run(cmd, stderr=log).returncode != 0:
            raise RuntimeError("ffmpeg failed while preparing a clip:\n" + log_path.read_text()[-2000:])


def render_video(payload: dict, fmt: str, long_edge: int, opts: dict, out: Path, progress, log_path: Path):
    target, _ = VIDEO_FORMATS[fmt]
    hdr = target == "hlg"
    photo_target = "p3" if hdr else target
    W, H = canvas_size(payload["canvas"]["aspect"], long_edge)
    fps = int(opts.get("fps", 30))
    tiles = _resolve(payload)
    duration = video_duration(tiles, opts.get("durationMode", "longest"), float(opts.get("customDuration", 0)))
    include_audio = opts.get("audio", True)
    cache = MediaCache(photo_target)
    bg = color.hex_to_target(payload["canvas"].get("bg", "#ffffff"), photo_target) + (255,)

    work = Path(tempfile.mkdtemp(prefix="collage_", dir=media.CACHE_DIR))
    log_path.write_text("")
    inputs: list[list[str]] = []
    graph: list[str] = []
    # HDR composites in 10-bit Y'CbCr 4:4:4 (BT.2020, HLG); SDR composites in 8-bit RGB.
    to_comp = "scale=out_color_matrix=bt2020:out_range=tv,format=yuva444p10le" if hdr else "format=rgba"
    ov_fmt = "yuv444p10" if hdr else "rgb"

    def add_input(args):
        inputs.append(args)
        return len(inputs) - 1

    layer = Image.new("RGBA", (W, H), bg)
    layer_tiles: list = []
    layer_has_content = True
    cur = None

    def flush_layer():
        nonlocal layer, layer_tiles, layer_has_content, cur
        if not layer_has_content:
            return
        if hdr:
            arr = np.asarray(layer)
            log2 = _gain_log2(layer_tiles, W, H)
            log2 = np.asarray(Image.fromarray(log2, "F").resize((W, H), Image.BILINEAR))
            rgba16 = np.empty((H, W, 4), np.uint16)
            rgba16[..., :3] = color.p3_to_hlg16(arr[..., :3], log2)
            rgba16[..., 3] = arr[..., 3].astype(np.uint16) * 257
            p = work / f"layer{len(inputs)}.raw"
            p.write_bytes(rgba16.astype("<u2").tobytes())
            idx = add_input(["-f", "rawvideo", "-pix_fmt", "rgba64le", "-s", f"{W}x{H}", "-framerate", str(fps),
                             "-stream_loop", "-1", "-i", str(p)])
        else:
            p = work / f"layer{len(inputs)}.png"
            layer.save(p, compress_level=1)
            idx = add_input(["-loop", "1", "-framerate", str(fps), "-i", str(p)])
        if cur is None:
            graph.append(f"[{idx}:v]{to_comp}[b{idx}]")
        else:
            graph.append(f"[{idx}:v]{to_comp}[l{idx}]")
            graph.append(f"[{cur}][l{idx}]overlay=0:0:format={ov_fmt}[b{idx}]")
        cur = f"b{idx}"
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer_tiles = []
        layer_has_content = False

    audio_inputs = []
    n_clips = sum(1 for _, m in tiles if m["kind"] == "video")
    prep_share = 0.35 if n_clips else 0.0
    done_clips = 0
    for t, m in tiles:
        if m["kind"] != "video":
            tile, pos = compose_tile(cache.image(m), t, W, H)
            _small_paste(layer, tile, pos)
            layer_tiles.append((t, m))
            layer_has_content = True
            continue
        flush_layer()
        x0, y0, tw, th = tile_box(t, W, H)
        vis = visible_region(t, tw, th, m["width"], m["height"])
        if not vis:
            continue
        start = float(t.get("start", 0))
        want_audio = include_audio and t.get("audio", True) and m["hasAudio"]
        seg = work / f"clip{len(inputs)}.mkv"
        _render_clip_segment(m, t, vis, (tw, th), tile_radius(t, W, H), start, fps, target, want_audio, seg, log_path)
        done_clips += 1
        progress(prep_share * done_clips / n_clips)
        vi = add_input(["-stream_loop", "-1", "-i", str(seg)])
        if want_audio:
            audio_inputs.append(vi)
        label = f"v{vi}"
        graph.append(f"[{vi}:v]setpts=PTS-STARTPTS,fps={fps}:round=near,{to_comp}[{label}]")
        cx, cy = x0 + tw / 2, y0 + th / 2
        graph.append(f"[{cur}][{label}]overlay=x='{cx:.2f}-w/2':y='{cy:.2f}-h/2':format={ov_fmt}:eof_action=repeat[o{vi}]")
        cur = f"o{vi}"

    if layer_has_content or cur is None:
        layer_has_content = True
        flush_layer()

    if hdr:
        graph.append(f"[{cur}]format=yuv420p10le,"
                     "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=tv[vout]")
    else:
        pix = "yuv420p10le" if fmt == "mov-hevc-p3" else "yuv420p"
        prim = "smpte432" if fmt == "mov-hevc-p3" else "bt709"
        graph.append(f"[{cur}]scale=out_color_matrix=bt709:out_range=tv,format={pix},"
                     f"setparams=color_primaries={prim}:color_trc=bt709:colorspace=bt709:range=tv[vout]")
    if len(audio_inputs) == 1:
        graph.append(f"[{audio_inputs[0]}:a]aresample=48000[aout]")
    elif audio_inputs:
        graph.append("".join(f"[{i}:a]" for i in audio_inputs)
                     + f"amix=inputs={len(audio_inputs)}:duration=longest:normalize=0,aresample=48000[aout]")

    cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1"]
    for args in inputs:
        cmd += args
    filter_file = work / "graph.txt"
    filter_file.write_text(";\n".join(graph))
    cmd += ["-/filter_complex", str(filter_file), "-map", "[vout]"]
    if audio_inputs:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    crf = int(opts.get("crf", 18))
    preset = opts.get("preset", "medium")
    if fmt == "mov-hevc-hlg":
        cmd += ["-c:v", "libx265", "-preset", preset, "-crf", str(crf + 2), "-tag:v", "hvc1",
                "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:range=limited:log-level=error",
                "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc"]
    elif fmt == "mov-hevc-p3":
        cmd += ["-c:v", "libx265", "-preset", preset, "-crf", str(crf + 2), "-tag:v", "hvc1",
                "-x265-params", "colorprim=smpte432:transfer=bt709:colormatrix=bt709:range=limited:log-level=error",
                "-color_primaries", "smpte432", "-color_trc", "bt709", "-colorspace", "bt709"]
    else:
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    cmd += ["-color_range", "tv", "-r", str(fps), "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out)]

    with open(log_path, "a") as log:
        log.write(" ".join(cmd) + "\n\n" + filter_file.read_text() + "\n\n")
        log.flush()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True)
        for line in proc.stdout:
            mt = re.match(r"out_time_us=(\d+)", line)
            if mt:
                progress(min(0.99, prep_share + (1 - prep_share) * int(mt.group(1)) / 1e6 / duration))
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed:\n" + log_path.read_text()[-2000:])
    shutil.rmtree(work, ignore_errors=True)
    return {"width": W, "height": H, "duration": duration}
