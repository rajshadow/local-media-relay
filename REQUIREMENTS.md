# Requirements and test checklist

Local Media Relay shares selected Windows folders over a trusted LAN. A TV, Quest headset, or any other device with a DLNA discovery and playback client can find the library and request media. The client determines which containers, codecs, projection modes, and transparency formats it can render.

This is a working checklist for the project. It tracks current behavior alongside the things I still want to build or try on real devices.

## Functional requirements

| ID | Requirement | Acceptance and current status |
| --- | --- | --- |
| F1 | Select one or more Windows folders. Save edits and restore the selection on launch. Start or stop sharing from one window. | Add, remove, relaunch, start, and close. Folder persistence and the start/close flow passed GUI checks. |
| F2 | Advertise the library via SSDP and expose a browseable DLNA catalog with nested folders. | Discover the library and open a nested folder on a TV, a Quest DLNA client, and another DLNA client. Synthetic discovery and SOAP Browse passed. Basic TV playback was observed. The catalog supports polling for changes in roots and browsed directories. UPnP event subscriptions and broader client checks are open. |
| F3 | Direct-play original videos and support HTTP byte-range seeking. Preserve the original file and its filename, including media tags. | Compare served bytes against the source and verify HEAD, full GET, range responses, and conditional ranges. Synthetic checks passed. A TV trial showed an intermittent seek hiccup on one clip. |
| F4 | Browse and display still images alongside videos, starting with JPEG and PNG. | A mixed folder lists photos as DLNA photo items with the correct MIME type. A compatible client opens both formats and receives the original bytes. Planned. The catalog currently exposes videos. |
| F5 | Carry flat, stereoscopic, and immersive video, including side-by-side, top/bottom, VR180, VR360, and alpha passthrough variants. | Use reference clips to verify that tags and bytes survive delivery, then record which client and version render each projection or alpha mode correctly. Synthetic filename and byte preservation passed. Projection, packed alpha rendering, and player-side chroma key behavior remain unverified over DLNA. |

The relay serves original media. Transcoding, projection conversion, and applying transparency are outside the server's scope. A file can be listed and transferred successfully even when a particular player cannot render it. [DeoVR playback and passthrough documentation](https://deovr.com/documentation)

## Non-functional requirements

| ID | Requirement | Acceptance and current status |
| --- | --- | --- |
| N1 | Support individual video files in the **50–100 GB** range with memory usage that stays bounded as file size increases. | Exercise files at both ends of the range. Verify file lengths and compare byte windows at the start, beyond 4 GiB, and near the end. Record peak server memory during playback and repeated seeking. The existing smoke and 512 MiB loopback benchmark do not establish this target. |
| N2 | Sustain high-bitrate direct playback and resume promptly after a seek on a suitable LAN. | On representative TV and Quest clients, record network conditions, clip bitrate, time to first frame, post-seek resume time, and visible stalls. Correlate failures with the request log. The loopback benchmark is a server-side baseline. Device and network baselines are open. |
| N3 | Limit serving to selected media and preserve the originals. Keep the management endpoint off the LAN. | Confirm that unselected paths, traversal attempts, linked entries, and unsupported files are rejected. Compare fixture bytes before and after serving. The tests cover traversal, replaced root junctions, and synthetic file gating. The service uses HTTP on a trusted LAN. The path check and file open are separate operations, so local users with write access to shared folders are trusted. |
| N4 | Own the session lifecycle and allow independent sessions to coexist. | Start two sessions and stop one. The other must still answer discovery and serve requests. Verify port and process cleanup on close. Synthetic isolation and the GUI close check passed. |
| N5 | Make playback issues diagnosable with logs that survive Stop. | A seek should leave its requested and returned ranges, HTTP status, duration, and bytes written in a per-session log. The smoke test verifies these fields. Logs measure HTTP behavior at the server. |
