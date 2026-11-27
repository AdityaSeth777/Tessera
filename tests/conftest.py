import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collage import media, render  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HAS_NODE = bool(shutil.which("node"))
IS_MACOS = sys.platform == "darwin"

needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
needs_node = pytest.mark.skipif(not HAS_NODE, reason="node not on PATH")
macos_only = pytest.mark.skipif(not IS_MACOS, reason="needs macOS")


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Point every workspace/exports path at tmp_path so tests never touch the real folders."""
    ws = tmp_path / "workspace"
    monkeypatch.setattr(media, "WORKSPACE", ws)
    monkeypatch.setattr(media, "MEDIA_DIR", ws / "media")
    monkeypatch.setattr(media, "CACHE_DIR", ws / "cache")
    monkeypatch.setattr(media, "INDEX", ws / "media" / "index.json")
    monkeypatch.setattr(render, "EXPORT_DIR", tmp_path / "exports")
    return ws
