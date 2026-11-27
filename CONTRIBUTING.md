# Contributing to Tessera

Thanks for helping. Tessera is a small project, so the process is light. These notes cover what keeps the app correct.

## Ground rules

- **Everything stays on the user's machine.** No network calls, telemetry, CDNs or remote fonts. Media is never sent anywhere.
- **Color is the product.** A change that makes colors slightly duller or drops HDR is a regression, even if the result looks fine at a glance.
- **No frontend build step.** `static/` is plain HTML, CSS and ES modules that the browser loads directly. Please keep it that way.
- Be kind. This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Setting up

You need macOS, Python 3 and ffmpeg (`brew install ffmpeg`).

```sh
git clone https://github.com/AdityaSeth777/Tessera.git
cd Tessera
./start.sh                 # creates .venv, installs requirements, serves http://127.0.0.1:5173
```

macOS is the only supported platform for now. CI also runs the tests on Ubuntu, but Display P3 currently fails there because the P3 color profile is loaded from a macOS system folder (that test is marked as an expected failure). Bundling the profile would fix it.

## Running the tests

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Tests that need ffmpeg or macOS are skipped automatically when those aren't available. CI runs the suite on macOS and Ubuntu for every push and pull request.

## Where things live

The [Project layout](README.md#project-layout) and [Architecture](README.md#architecture) sections of the README map out the code. In short:

| Area | Files |
| --- | --- |
| Editor UI, state, undo | `static/js/app.js` |
| Layout math, templates, auto layout | `static/js/layout.js` |
| HTTP server, security checks, export jobs | `server.py` |
| Import, previews, proxies | `collage/media.py` |
| Color transforms, LUTs, tone mapping | `collage/color.py` |
| HDR gain maps | `collage/gainmap.py` |
| Compositing stills and video | `collage/render.py` |

## Things that must stay in sync

- **Placement math exists twice:** in `layout.js` for the preview and in `render.py` for the export. If you change crop, pan or zoom in one, change the other the same way, or exports will stop matching the editor.
- **The browser owns layout.** The server receives final normalized rectangles and never recomputes the layout. Keep it that way.
- **Clip timing:** the preview transport and the video exporter both use `clip_time(t) = start + (t mod (duration − start))`.

## Changing color or HDR code

Changes to `color.py`, `gainmap.py` or the color parts of `render.py` need evidence, not just "looks right":

- Add or update a test with known reference values. The README's [How it was verified](README.md#how-it-was-verified) table lists the ones already used.
- If you change a file format writer, say which viewers you opened the output in (Preview, Photos, Chrome, an Android phone) and on what kind of screen (SDR or HDR).
- Note any bit-depth effects. Some ffmpeg filters silently drop to 8-bit; see the table in section 7.10 of the README.

## Changing the server

Every route goes through `local_only()` in `server.py`. New routes must not weaken it. If you add a route that writes or deletes anything, add a test showing that a request from a foreign `Origin` is rejected.

Found a security problem? Report it privately as described in [SECURITY.md](SECURITY.md), not in a public issue.

## Pull requests

1. Fork the repo, branch from `main`, and keep each PR to one change.
2. Run the tests.
3. For UI changes, add a screenshot or short screen recording.
4. Add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).
5. Write commit messages in the imperative ("Add carousel export", not "Added").

## Reporting bugs

Use the bug report template. The most useful details are your macOS version, Mac model (Apple silicon or Intel), the ffmpeg version, and the kind of file involved (iPhone HEIC, HDR, video, and so on). If you can share a sample file, that helps a lot. Only share files you're happy to make public.
