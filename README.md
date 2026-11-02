# local-collage

A collage maker that runs entirely on your Mac. Photos and videos never leave the machine.

- **HEIC stays HEIC.** iPhone photos are decoded directly (libheif) in their **Display P3** wide color, composited in P3, and only encoded once at export — no JPEG round trip, so the "pop" survives.
- **Mix anything:** HEIC, JPEG, PNG, TIFF, WebP, AVIF + MOV/MP4 (including iPhone HDR/HLG video).
- **Videos play inside their tile.** A collage with clips exports as a video (MP4 or HEVC P3 MOV); shorter clips loop.
- **Layouts for 1–10 tiles:** auto layout (picks the arrangement that crops your photos least), a gallery of templates, draggable dividers, plus freeform mode (move, resize, rotate, overlap).
- **Style:** any canvas format (1:1, 4:5, 9:16, 16:9, print sizes, custom), sharp / rounded / circle tiles, spacing + border sliders, padding color by picker, hex or swatch.

## Run

```sh
./start.sh
```

Opens http://127.0.0.1:5173. The first run creates a Python venv and installs Flask, Pillow, pillow-heif and numpy. Requires Python 3 and `ffmpeg` (`brew install ffmpeg`). No Xcode needed.

Exports are written to `exports/`, imported media is kept in `workspace/`.

## Using it

| Action | How |
| --- | --- |
| Add media | Drop files onto the window, or **Add files**. Click a library item to add it |
| Reposition photo in its tile | Drag inside the tile |
| Zoom | Scroll / pinch on the tile, or the Zoom slider |
| Resize cells | Drag the gap between tiles |
| Swap two tiles | Drag a tile's ⠿ handle onto another tile |
| Replace a tile | Drag a library item onto it |
| Free a tile | ⧉ on the tile (or Freeform mode for all). Then drag to move, corner handle to resize (⇧ = free aspect), top handle to rotate (⇧ = 15° steps). ⌥-drag pans the photo |
| Undo / redo | ⌘Z / ⇧⌘Z |
| Play videos | Space, or the transport bar under the canvas |

## Export formats

| Format | Color | Notes |
| --- | --- | --- |
| JPEG Display P3 | P3 | Recommended for photos — wide color, opens everywhere |
| HEIC Display P3 | P3 | Smallest, Apple-native |
| PNG / TIFF Display P3 | P3 | Lossless |
| JPEG / PNG sRGB | sRGB | Max compatibility; out-of-sRGB colors are clipped |
| MP4 H.264 | BT.709 | For Instagram, WhatsApp, web |
| MOV HEVC 10-bit | Display P3 | Wide color video for Apple devices |

HDR (HLG/PQ) iPhone video is tone-mapped to SDR with a 3D LUT generated locally (this ffmpeg build has no zscale).

## Known limitation

iPhone HEIC photos can carry an HDR **gain map** that brightens highlights on XDR screens. Exports keep the full Display P3 color but not the gain-map brightness boost.
