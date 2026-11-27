# Security policy

Tessera runs a small web server on your Mac. Any website open in your browser can try to talk to it, so security bugs here matter even though nothing is hosted online.

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately instead:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the problem, how to reproduce it, and what an attacker could do with it.

The maintainer aims to reply within 7 days. Once a fix is released, you'll be credited in the changelog unless you'd rather stay anonymous.

## What counts

In scope:

- A website reading your library, previews or exports (for example through DNS rebinding)
- A website triggering uploads, exports or deletes (cross-site requests)
- Path traversal or reading files outside `workspace/` and `exports/`
- Crafted image or video files that make Tessera run code or write outside its folders
- Script injection through file names or metadata shown in the editor

Out of scope:

- Attacks that need someone who already has access to your user account
- Bugs in ffmpeg, libheif, Pillow or other dependencies themselves. Report those upstream, but do tell us if Tessera uses them in an unsafe way.

## How Tessera protects you today

The server listens on `127.0.0.1` only, rejects requests with a foreign `Host` header, and rejects writes from other origins. The [Security model](README.md#security-model) section of the README explains each check.

## Supported versions

Only the latest commit on `main` gets security fixes.
