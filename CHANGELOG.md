# Changelog

All notable changes to Tessera are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- MIT license, contributing guide, code of conduct, security policy, and issue and pull request templates.
- Automated test suite and CI on macOS and Ubuntu.
- Launch video and a rewritten README.

### Changed
- Renamed the project from local-collage to **Tessera**.

## [0.1.0] - 2026-11-02

First working version.

### Added
- Collage editor for 1–10 tiles: auto layout, a gallery of up to 106 templates, draggable dividers and freeform mode.
- Native HEIC decoding in Display P3, with one encode at export.
- iPhone HDR gain maps carried into Ultra HDR JPEG and Apple HDR HEIC exports.
- Video tiles with frame-accurate looping from a chosen start point, plus MP4 H.264, HEVC P3 and HEVC HLG HDR exports.
- BT.2446 Method A tone mapping for HDR video shown in SDR.
- Server that only listens on `127.0.0.1`, with Host and Origin checks.
