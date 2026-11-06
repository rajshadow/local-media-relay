# Local Media Relay

Share a few Windows folders and play videos or open photos from a TV, Quest, or other device with a DLNA player.

## Run it

You’ll need Python 3.11+ with Tkinter and `pythonw.exe` on PATH, plus Caddy. Put the native `caddy.exe` on PATH or in `tools/`. Chocolatey installs are also picked up.

Open **Start Local Media Relay.cmd**, add folders, and click **Start sharing**. On the player, look for **Local Media Relay** under network media sources. Folder selections are saved for the next launch. The `fixtures/` folders contain two short test clips.

Use a trusted LAN. Sharing is open to reachable clients by default. To restrict it, enable **Only these IPv4 addresses** and enter the players’ addresses before starting. This filters discovery, browsing, thumbnails, and playback. Access settings save on Start.

**Previously seen clients** lists addresses from recent access logs, with this PC excluded and one row per IP, a device guess, reported client strings, activity seen, and last-seen time. Connect a player while sharing, then refresh the list. Stop sharing, select entries, and click **Add selected IPs** to prepare the allowlist for the next Start. The activity column distinguishes description fetching, library requests, and media requests. It uses up to 30 days of recent log tails. Addresses and reported names can change.

JPEG and PNG photos are supported. If ffmpeg is on PATH, previews are generated in the background and cached under `%LOCALAPPDATA%\Local Media Relay\thumbnails`. To supply your own preview, put a JPEG named `movie.mp4.thumb.jpg` beside `movie.mp4` (or `photo.png.thumb.jpg` beside a photo). It must be newer than the source and at most 2 MiB.

Compatible Samsung-style DLNA players can save and read resume positions. Checkpoints are stored per file and client IP. Player support, IP changes, and real-device seek behavior still need checking.

## How it works

```text
DLNA player ── discover ──> Python (SSDP)
DLNA player ── browse ────> Caddy ──> Python (catalog)
DLNA player ── play/seek ─> Caddy ──> selected files, after a Python path check
```

Python checks which files can be shared. Caddy serves the bytes and handles HTTP details such as ranges and `If-Range`. The folder picker starts and stops both together. [Caddy file server](https://caddyserver.com/docs/caddyfile/directives/file_server) · [Caddy implementation](https://github.com/caddyserver/caddy/blob/v2.11.1/modules/caddyhttp/fileserver/staticfiles.go)

| Choice | Why |
| --- | --- |
| DLNA over manual URLs | Use the player’s existing network library. |
| Tkinter and JSON over a browser UI | A native folder picker and a saved list are enough. |
| Caddy over serving bytes in Python | One HTTP listener handles file serving, forwards catalog requests, and logs both. Python’s basic file handler was faster on a single full read in the [loopback benchmark](benchmark.py). A Go helper could also handle ranges, but it would need its own routing and logging code. [Go ServeContent](https://pkg.go.dev/net/http#ServeContent) |

## Testing

Install the development tools with `python -m pip install -r requirements-dev.txt`.
Run `python -m ruff check .` for linting and `python -B -m unittest discover -s tests -v` for regression tests. Both run automatically on pushes and pull requests.
Run `python -B smoke.py` for the synthetic discovery, browse, and seek checks. The [test checklist](REQUIREMENTS.md) tracks real-device playback, VR and alpha checks, and future device selection.

Add `--large-files` to check byte ranges in sparse 50 GB and 100 GB test files. The Windows temporary directory must support sparse files. The [checklist](REQUIREMENTS.md#device-access-ideas) also tracks ideas for choosing which devices can connect.
