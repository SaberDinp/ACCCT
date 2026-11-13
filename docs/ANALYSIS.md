# Understanding a recorded lap

ACCC Telemetry reads the visible ACC HUD in recorded gameplay. The desktop app
analyzes recordings locally and lets you review them against another lap. It does
not collect live game telemetry or use delayed broadcasts for live driving advice.

## Import and select

Open **Video options** for **Your run** or **Reference**, then use
**Import recording** to add a video.
Local files work without Internet access. Supported public YouTube recordings,
Twitch VODs/clips, and direct video URLs are downloaded for local analysis.
Private, expired, restricted, or unavailable videos can fail to import; use an
accessible local recording in that case. The app does not request platform
passwords or bypass sign-in restrictions.

For a ready-made reference choice, open **Reference → Video options → Find
reference lap** and select a circuit and car. Choose among the available creator
laps, preview the original if you like, then press **Import**. Browsing
the library does not download videos. The first import saves the recording and
analysis locally; selecting it again reuses that saved recording, including after
restarting the app. Deleting it, removing its source video, or a failed import
allows a fresh attempt.

The picker lists every ACC car variant and circuit, but public reference coverage
is incomplete. A missing combination offers the option to paste your own video
link. Lap times and game versions are creator stated; patches, weather, fuel,
and balance of performance can differ. The catalog verifies identities from
creator metadata, not successful telemetry extraction from every full video.
See [reference coverage and sources](../assets/references/README.md).

**Open video in browser** is available for both recordings. Imported public
recordings open their original page. Local files open an app-served player with
normal video controls and sound; keep the desktop app open while watching.
If the browser cannot decode the audio, **Use compatible sound** prepares a
local audio copy. Unsupported video formats can be downloaded from the player
and opened in a video player.

Each recording remains available in the comparison area's **Video options**.
The app starts with detected lap boundaries when available. Use **Start of lap**
and **End of lap** to pick the source frames and press **Lock**. A reference is optional for reviewing one
recording. Choose recordings from the same circuit layout; known different
circuit layouts cannot be compared.

No sample race videos or existing session libraries are shipped in the source
repository. Start with your own footage or a public recording you may download.

### YouTube downloads and sign-in

The app attempts public YouTube downloads without an account. It uses its own
downloader and does not read your browser's cookies, so signing into YouTube in
your browser does not sign the app in. Desktop installers include the JavaScript
runtime needed by the downloader.

If YouTube refuses a video-data request with HTTP 403, version 1.4.1 and later
retry once using an alternative public stream, when one is available. The retry
keeps the same download time and size limits and still does not use an account.
HTTP 403 by itself does not establish that sign-in is required.

Public videos can still fail because of a temporary network error, YouTube
verification or rate limits, or a change to the site's download formats. Private,
members-only, age-restricted, removed, and region-restricted videos may not be
available anonymously. Use another accessible reference or import a local copy
you are allowed to download when the site requires access the app cannot provide.

If an import fails, open **File → Show Library Folder** in the desktop app, then
open `data/references/<import ID>/download.log` using the ID in the error message.
The final `ERROR` line and preceding `WARNING` lines explain the downloader's
failure. Include those lines, the video URL, your operating system, and app version
when reporting the problem. Older app versions use the same generic
“may require sign-in” message for every download error; that message alone does
not establish that the video requires an account.

## What can be measured

| Channel | What the video can show |
| --- | --- |
| Brake and throttle | Visible HUD bar length: game-input percentage, not pedal force. |
| Speed and gear | Template recognition of HUD digits; unreadable observations stay missing. |
| Steering | HUD marker position, normalized left/right; not steering-wheel degrees. |
| RPM | Small HUD digits when legible; compression can make readings unreliable. |
| Lap clock and validity | Visible timer and display color when readable. |
| Distance and acceleration | Estimates from HUD speed, not GPS or accelerometers. |
| Braking events | Peak, duration, release, speed changes, and reapplication within continuous observations. |
| Pedal habits | Time-weighted braking, throttle, coasting, and overlap. |

Hidden inputs cannot be reconstructed. Suspension travel, tire forces, wheel
speeds, temperatures absent from the HUD, exact track position, and a measured
racing line are outside this tool's scope. Confidence describes image matching;
it is not a calibrated probability. CSV exports include units and uncertainty.

## Read the comparison

Your run uses a **solid cyan** line; the reference uses a **dashed amber** line.
Positive time delta means your selected run is slower. Negative means faster.
Time and estimated-distance axes answer different questions, so compare the
same physical section of a lap when studying a braking point.

Different cars on the same circuit can be compared, but car, setup, tires, fuel,
weather, game version, and balance of performance all affect the result. A time
difference is a measurement of these recorded runs, not a ranking of the cars or
proof that every difference comes from driver technique. Gear numbers do not
represent equal ratios across cars.

The source videos, plots, time delta, and circuit markers share a review cursor.
Use video frames to check qualitative track position. The map shows approximate
lap progress along the centerline; it does not measure which racing line was used.

## Boundaries and physical alignment

A detected timer reset is a useful starting point, but the visible clock may be
hidden, rounded, or compressed. **Start of lap** and **End of lap** open a frame
picker whose title identifies the boundary. Seek to the start/finish crossing,
then press **Lock**. Pick the same physical line in both recordings. Locking one
boundary changes only that boundary and remembers the choice automatically.

Distance comes from integrating visible speed. Locking both lap starts confirms the physical crossing used in the
distance-based comparison; until then, time delta is explicitly approximate. Existing reviewed physical markers are still honored.
The app does not shift braking or throttle traces to make the drivers look alike.

## Automatic track guide

The guide always places markers using estimated lap progress. It can attempt to
select a circuit from the chosen lap's telemetry, using lap distance and the signed steering pattern when available.
Incomplete or ambiguous data may leave the circuit unselected. Select the circuit yourself whenever needed.
Your saved manual circuit choice takes priority over automatic suggestions.
These are estimates from video, not GPS or measured car position.

## Missing data and interrupted laps

Hidden HUDs, timestamp jumps, frozen footage, unreadable inputs, and stopped
driving break continuity. Menus create missing values instead of invented zero
inputs. Distance and braking events are not inferred across long gaps.

For comparison distance only, an isolated speed gap of at most 0.5 seconds may be
estimated when adjacent readings pass plausibility checks. Original speed stays
missing, and repaired intervals are disclosed. Cuts and longer gaps break delta
locally. An interrupted lap is not presented as a complete lap.

## Footage quality

HUD detection automatically locates supported ACC instrument clusters, including
current and legacy overlays, at their visible scale and position. Units are
standardized to km/h when they can be read; ambiguous units suppress speed rather
than corrupt distance. The legacy overlay has no usable steering strip.

Use footage with the complete pedal fills, digits, and lap clock visible.
Different fonts, very small HUDs, hidden input bars, and severe compression may
require clearer source footage. Invisible data cannot be recovered.

The bundled [car/circuit catalog](../assets/catalog/acc.json) is a selection aid,
not a claim that every car, camera, HUD, and circuit combination has been validated.
The [track-data documentation](../assets/tracks/README.md) describes geometry,
provenance, attribution, and licenses.

## Exports and storage

Use **Export CSV** in a recording's **Video options** for ordinary tables with
units, not proprietary MoTeC `.ld` files.
The comparison's **Export for AI** button downloads a bounded JSON summary of
the currently compared ranges. It does not call an AI service or send your data
anywhere. You choose whether to share the exported file with another tool.

Your library holds the session database, recordings, profiles, and local caches
separately from the application. Updating or replacing the app preserves it.
Before moving a library between computers, note that older sessions may reference
absolute video paths. See [library and backup instructions](../desktop/README.md#existing-recordings-and-settings).
