# Requirements and test checklist

Local Media Relay shares selected Windows folders over a trusted LAN. A TV, Quest headset, or any other device with a DLNA discovery and playback client can find the library and request media. The client determines which containers, codecs, projection modes, and transparency formats it can render.

This is a working checklist for the project. It tracks current behavior alongside the things I still want to build or try on real devices.

## Functional requirements

| ID | Requirement | Acceptance and current status |
| --- | --- | --- |
| F1 | Select one or more Windows folders. Save edits and restore the selection on launch. Start or stop sharing from one window. | Add, remove, relaunch, start, and close. Folder persistence and the start/close flow passed GUI checks. |
| F2 | Advertise the library via SSDP and expose a browseable DLNA catalog with nested folders. | Discover the library and open a nested folder on a TV, a Quest DLNA client, and another DLNA client. Synthetic discovery and SOAP Browse passed. Basic TV playback was observed. The catalog supports polling for changes in roots and browsed directories. UPnP event subscriptions and broader client checks are open. |
| F3 | Direct-play original videos and support HTTP byte-range seeking. Preserve the original file and its filename, including media tags. | Compare served bytes against the source and verify HEAD, full GET, range responses, and conditional ranges. Synthetic checks passed. A TV trial showed an intermittent seek hiccup on one clip. |
| F4 | Browse and display still images alongside videos, starting with JPEG and PNG. | A mixed folder lists photos as DLNA photo items with the correct MIME type. A compatible client opens both formats and receives the original bytes. Implemented. Synthetic delivery and photo-class checks cover JPEG/PNG. Real-client display, orientation and transparency checks remain. |
| F5 | Carry flat, stereoscopic, and immersive video, including side-by-side, top/bottom, VR180, VR360, and alpha passthrough variants. | Use reference clips to verify that tags and bytes survive delivery, then record which client and version render each projection or alpha mode correctly. Synthetic filename and byte preservation passed. Projection, packed alpha rendering, and player-side chroma key behavior remain unverified over DLNA. |

The relay serves original media. Transcoding, projection conversion, and applying transparency are outside the server's scope. A file can be listed and transferred successfully even when a particular player cannot render it. [DeoVR playback and passthrough documentation](https://deovr.com/documentation)

## Non-functional requirements

| ID | Requirement | Acceptance and current status |
| --- | --- | --- |
| N1 | Support individual video files in the **50–100 GB** range with memory usage that stays bounded as file size increases. | The optional sparse-file smoke passed at 50 GB and 100 GB. It verifies reported length and exact byte ranges at the start, beyond 4 GiB, and at the end. Sustained playback and peak memory on real large clips remain untested. |
| N2 | Sustain high-bitrate direct playback and resume promptly after a seek on a suitable LAN. | On representative TV and Quest clients, record network conditions, clip bitrate, time to first frame, post-seek resume time, and visible stalls. Correlate failures with the request log. The loopback benchmark is a server-side baseline. Device and network baselines are open. |
| N3 | Limit serving to selected media and preserve the originals. Keep the management endpoint off the LAN. | Confirm that unselected paths, traversal attempts, linked entries, and unsupported files are rejected. Compare fixture bytes before and after serving. The tests cover traversal, replaced root junctions, and synthetic file gating. The service uses HTTP on a trusted LAN. The path check and file open are separate operations, so local users with write access to shared folders are trusted. |
| N4 | Own the session lifecycle and allow independent sessions to coexist. | Start two sessions and stop one. The other must still answer discovery and serve requests. Verify port and process cleanup on close. Synthetic isolation and the GUI close check passed. |
| N5 | Make playback issues diagnosable with logs that survive Stop. | A seek should leave its requested and returned ranges, HTTP status, duration, and bytes written in a per-session log. The smoke test verifies these fields. Logs measure HTTP behavior at the server. |

## Device access ideas

- **Exact IPs.** Implemented with a saved list of IPv4 addresses. Restricted mode suppresses multicast announcements and answers approved peers only. Caddy checks actual peer addresses on all media and catalog routes. The synthetic integration test checks approved discovery and playback, denied metadata/media/thumbnail URLs, and spoofed forwarding headers. Some players may rely on announcements, so test discovery on the TV and Quest. [Caddy remote IP matcher](https://caddyserver.com/docs/caddyfile/matchers#remote_ip)
- **Previously seen clients.** Implemented from recent HTTP access logs, showing IP, last seen, a guess from the reported User-Agent, and the raw client string. Selection explicitly adds addresses to the allowlist. The log reader is bounded and tolerates malformed or incomplete records. It covers HTTP clients that contacted the relay. Network inventory from the Windows neighbor cache and discovery traffic remains future work, alongside manual labels. [Windows neighbor cache](https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netneighbor)
- **Separate address stability from identity.** A DHCP reservation can keep the IP steady on a managed home network. MAC addresses and advertised names can help match a device in the picker, but they are hints and can change. Require confirmation before moving access to a new IP. Stronger IP-independent identity would require a client that can present a credential, for example a client certificate. Treat that as a separate mode for clients that support it. [Private Wi-Fi addresses](https://support.apple.com/en-us/102509) · [Caddy client authentication](https://caddyserver.com/docs/caddyfile/directives/tls#client_auth)

## Playback and previews

The first implementations below have automated coverage. Player behavior still needs device testing.

- **Playback checkpoints.** Samsung-style `X_SetBookmark` and `dcmInfo` support is implemented with millisecond wire values and a persistent store. Tests cover timestamp units, restart persistence, separate clients, concurrent writes, replaced media and near-zero stop events. Client identity currently uses the actual peer IP. Device or profile identities that survive address changes and other player adapters remain future work.
- **Video thumbnails.** Implemented using optional ffmpeg background workers and a persistent cache. Jobs and queue size are bounded. Cached previews remain available without ffmpeg, and originals are served independently. Tests cover duplicate requests, stale cache entries, shutdown, and real generation from a synthetic clip. Large-clip preview timing remains open.
- **Image thumbnails.** Implemented with the same worker and cache. Previews are JPEG. A synthetic PNG passed original delivery and preview generation. Large images, EXIF orientation, transparency rendering, and player retries while previews are pending remain device checks.
- **Reuse existing previews.** Implemented for cached previews and `<source filename>.thumb.jpg` sidecars. Cache identity includes source path, size, nanosecond modification time, and preview settings. Sidecars must be valid ordinary files inside the selected library, at most 2 MiB, and newer than their source. Cache hits, invalidation, duplicate requests, and linked entries are tested. Importing existing JSON thumbnail maps is future work.
