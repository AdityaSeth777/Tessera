import os
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from collage import media, render

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/static")

jobs: dict[str, dict] = {}


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/media")
def list_media():
    items = sorted(media.load_index().values(), key=lambda m: m.get("added", 0))
    return jsonify([media.public(m) for m in items])


@app.post("/api/media")
def upload_media():
    out, errors = [], []
    for f in request.files.getlist("files"):
        name = os.path.basename(f.filename or "upload")
        fd, tmp = tempfile.mkstemp(dir=media.CACHE_DIR, suffix=Path(name).suffix)
        os.close(fd)
        f.save(tmp)
        try:
            meta = media.ingest(Path(tmp), name)
            out.append(media.public(meta))
        except Exception as e:
            traceback.print_exc()
            Path(tmp).unlink(missing_ok=True)
            errors.append({"name": name, "error": str(e).splitlines()[0][:300] if str(e) else type(e).__name__})
    return jsonify({"items": out, "errors": errors})


@app.delete("/api/media/<mid>")
def delete_media(mid):
    media.delete(mid)
    return jsonify({"ok": True})


@app.get("/m/<mid>/<name>")
def media_file(mid, name):
    if name not in ("preview.jpg", "thumb.jpg", "proxy.mp4") or not media.get(mid):
        abort(404)
    resp = send_from_directory(media.media_dir(mid), name, conditional=True)
    resp.headers["Cache-Control"] = "max-age=31536000, immutable"
    return resp


@app.post("/api/export")
def start_export():
    body = request.get_json(force=True)
    fmt = body["format"]
    if fmt not in render.STILL_FORMATS and fmt not in render.VIDEO_FORMATS:
        abort(400, "unknown format")
    jid = uuid.uuid4().hex[:10]
    jobs[jid] = {"status": "running", "progress": 0.0, "started": time.time()}
    threading.Thread(target=_run_export, args=(jid, body), daemon=True).start()
    return jsonify({"id": jid})


def _run_export(jid, body):
    job = jobs[jid]

    def progress(p):
        job["progress"] = p

    try:
        render.EXPORT_DIR.mkdir(exist_ok=True)
        fmt, payload = body["format"], body["collage"]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        long_edge = int(body.get("longEdge", 4096))
        if fmt in render.STILL_FORMATS:
            ext = render.STILL_FORMATS[fmt][2]
            out = render.EXPORT_DIR / f"collage-{stamp}{ext}"
            img = render.render_still(payload, render.STILL_FORMATS[fmt][0], long_edge,
                                      lambda p: progress(p * 0.8))
            render.save_still(img, fmt, out, int(body.get("quality", 92)))
            job.update(width=img.width, height=img.height)
        else:
            ext = render.VIDEO_FORMATS[fmt][1]
            out = render.EXPORT_DIR / f"collage-{stamp}{ext}"
            info = render.render_video(payload, fmt, long_edge, body.get("video", {}), out, progress,
                                       media.CACHE_DIR / f"export-{jid}.log")
            job.update(info)
        job.update(status="done", progress=1.0, file=out.name, path=str(out),
                   size=out.stat().st_size, elapsed=time.time() - job["started"])
    except Exception as e:
        traceback.print_exc()
        job.update(status="error", error=str(e)[-1500:])


@app.get("/api/export/<jid>")
def export_status(jid):
    job = jobs.get(jid)
    if not job:
        abort(404)
    return jsonify(job)


@app.get("/exports/<path:name>")
def export_file(name):
    return send_from_directory(render.EXPORT_DIR, name, as_attachment=request.args.get("dl") == "1")


if __name__ == "__main__":
    media.load_index()
    port = int(os.environ.get("PORT", 5173))
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Collage running at {url}\n  Exports are saved to {render.EXPORT_DIR}\n")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
