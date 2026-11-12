# Validation - 25 September 2026

> Historical development log. This records earlier experiments and checks, not
> the current app's feature list or release certification. Live monitoring, phone
> streaming, and the associated browser checks were retired for the recorded-lap
> workflow. Some commands and local evidence paths below no longer exist in a
> clean checkout. Use [the contributor guide](docs/DEVELOPMENT.md) for current
> checks and [GitHub Releases](https://github.com/SaberDinp/ACCCT/releases) for
> the test scope of a particular desktop version.

## Tested Environment

This Mac, macOS arm64, Python 3.14.3, OpenCV 5.0, PyAV 18.1 and Chromium 153.
Exact package versions are in `requirements-lock.txt`. The sample is the supplied
`acc_video.mp4`: 1920 x 1080, approximately 29.947 fps, 16,340 decoded frames,
545.64 seconds container duration. No external AI inference was used.

## Automated Checks

Latest combined suite: **1,206 tests passed, plus 96 parameterized subtests**
(1,202 tests / 92 subtests in the full run, then the final dashboard-only test /
four subtests separately, plus three new Evo-generation title cases in a passing
889-test focused rerun). This includes a 775-case metadata/comparison matrix;
it does not represent 775 footage validations. Logs: `artifacts/expansion-pytest.log`
and `artifacts/expansion-dashboard-test.log`; the final identity regression run is
`artifacts/expansion-identity-tests.log`.
The run emits one upstream FastAPI/Starlette TestClient deprecation warning.

- Color input bars, four fill directions, amber highlights, white labels,
  malformed fills and hidden HUD rejection.
- 23 held-out actual-video speed/gear frames, actual menu screens, invalid red
  clock, compressed amber inputs, 720p scaling, mph conversion and missing-digit
  rejection. These labels were inspected from the footage, not collected from
  the game physics engine.
- Time-weighted metrics, partial braking events, release time, reapplications,
  timestamp gaps, stale and stationary observations, and no gap interpolation.
- Lap reset confirmation, short missing-clock intervals, timer jump rejection,
  invalid/interrupted laps and gross speed outlier rejection with rounding tolerance.
- SQLite durability, sample export, source lifecycle, simulated network failures,
  pause/resume/seek, stale frames, old-session frame isolation and restart recovery.
- Recording deletion, cascading/queued-sample cleanup, explicit managed-video
  removal, shared/original/symlink safeguards, source/import/search busy checks,
  localhost permissions, import-link invalidation, and concurrent frame decoding.
- Actual video decoding, Twitch VOD processing-path selection, profile validation,
  original-time recording previews, phone WebSocket payload and LAN read-only controls.
- Automatic HUD translation/scaling, English/German anchors, actual reference
  frames at 10/20/45/180 seconds, layout changes, menu recovery, and steering
  polarity/confidence/missing values. Legacy saved profiles retain user settings
  while gaining default steering regions in memory.
- Independent import workers, live-session isolation, cancellation and failure
  durability, resource limits, per-import profiles/roles, and nonzero/reset PTS.
  Imports retain no historical samples in the analyzer after SQLite persistence.
- Cross-session ranges, stepped gear, confidence-checked steering, unit mismatch,
  missing intervals, gated time delta, and bounded feature-preserving plots.
  Short observed pedal spikes survive coarse plots; saturated feature budgets
  are reported instead of claiming every event survives arbitrary downsampling.
- Single-recording distance/time traces, exact selected boundaries, preserved
  gaps and channel availability, no reference/delta values, and read-only API
  behavior that leaves recording samples and lap records unchanged.
- Persisted reviewed ranges, atomic rejection of invalid boundaries/markers,
  discontinuity rejection, preservation of raw observations and detected laps,
  and nearest-frame preview timestamps. Crossing proposals are chronological
  so a higher-scoring later replay does not displace an earlier candidate.
- Six checksummed, licensed circuit geometries; closure, scale, aspect ratio,
  invalid inputs, progress projection, reverse direction, crossing ambiguity and
  independent returned data. Track API reads do not create sessions, unknown
  layouts return 404, and no circuit asserts a verified ACC start/finish origin.
- Bounded short speed-gap estimates without filling the original speed channel;
  exact between-frame IN/OUT boundaries; endpoint and paired-landmark registration
  preserving elapsed time; rejection of cuts, stops, nonmonotonic anchors and
  unconfirmed physical endpoints.
- Clock boundary backprojection, clock-only dropout recovery, partial first laps,
  gaps and seeks; old-session boundary refinement without changing stored data.
- Visual matching scale/lateral-shift behavior, cross-camera view changes, static
  cockpit/menu rejection, synthetic lap recurrence, bounds, progress and cleanup.
  Single-worker lifecycle, cancellation, copied settings, shutdown and read-only
  lap/landmark APIs are tested with isolated databases.

## GT3 / Circuit Expansion

The completed campaign analyzed **42 cached public YouTube videos**, totaling
**153.53 minutes and 223,427 sampled frames**. Every current full trace was checked
for its expected row count, monotonic timestamps and final source time. All runs
completed without analysis errors. Thirty videos yielded speed, gear and pedals;
twelve yielded no supported HUD observations. Clips include non-driving footage,
so observed-channel percentages are not accuracy percentages.

After correcting spaced **Evo 2** discovery aliases, the clips' title hints span
22 GT3 variants and 24 circuits; observations of the core channels span 20 variants
and 18 circuits. Nine variants and Zandvoort remain without tested footage. Sixteen
new download attempts were blocked by YouTube sign-in challenges; no account or
cookie access was attempted. The complete result table, source links, remaining
gaps and before/after coverage are in
[the expansion report](artifacts/expansion-evaluation.md), with machine-readable
evidence paths in `artifacts/expansion-evaluation.json`. Title hints remain
unverified identities and none of these runs certifies all cameras or complete laps.

The Mustang/Indianapolis full-video speed observation rate increased **0 → 59.05%**
(74.97% of detected-HUD frames). Same-schedule sparse checks improve Mustang /
Donington 0 → 50% and Ferrari / Valencia 0 → 75%. These are availability gains,
with the separate independently read frames below checking recognition accuracy.

The existing catalog was rechecked against official content sources: 31 distinct
GT3 variants and 25 circuits. All 775 combinations are exercised for identity,
cross-car comparison and preserved measured endpoint/segment deltas. The ten
licensed matching circuit maps remain separate from this catalog; fifteen
circuits support telemetry and comparison without a bundled map.

Visual lap search now uses a 900-second bound for Nürburgring 24h and unknown
circuits, 300 seconds for other labeled circuits, and permits explicit bounds up
to 1800 seconds. Real feature matching on synthetic moving scenes covers 720-
and 1200-second laps, and clock tests cover laps up to 1200 seconds. A cached wet
Honda/Nürburgring video was visually inspected beyond eight minutes. Its video
cuts before a confirmable finish; no complete lap was certified. See
`artifacts/track-expansion/README.md` for source timestamps and images.

Recognition changes add softened unit templates, a stricter luminance fallback
for dashboard-tinted speed numerals, and two pixels of calibration tolerance at
the rightmost digit. Twelve independently read native crops from Mustang /
Indianapolis, Mustang / Donington and Ferrari / Valencia improve from 2/12 to
11/12 exact speeds and 3/12 to 12/12 units. Neither version accepted an incorrect
non-missing speed. One obscured numeral stays unavailable. These source crops
are regression fixtures, not new OCR training templates.

Twenty additional independently transcribed full frames across twenty title-
labeled car variants give 17 exact speed/gear/unit matches, three missing speed
readings, and zero wrong readings. The three remaining cold-frame gaps are
explicitly recorded in `tests/fixtures/expansion_hud_labels.json`; the standalone
`scripts/check_expansion.py` deliberately exits nonzero while they remain.
The sixteen prior independent frames pass all five channels. These frame checks
do not certify complete laps or raw game telemetry accuracy.

An independent perturbation check found 417 correct / 543 missing / zero wrong
speed readings over 960 tinted or blurred digit cases (before: 172 / 788 / zero).
Across 320 unit cases, correct units rose 147 to 208 with zero wrong units. All
624 structured non-unit negatives were rejected. Reports:
`artifacts/hud-refinement/report.json`,
`artifacts/hud-fallback-independent-review.json`,
`artifacts/expansion-frame-evaluation.json`, and
`artifacts/expansion-five-channel-evaluation.json`.

Paired full-import measurements (two repetitions, identical 30-second clips,
temporary databases) reduced AV1 wall time 8.136 → 7.650 seconds and ultrawide
VP9 13.667 → 11.507 seconds. H.264 remained 6.461 → 6.449 seconds. All stored
telemetry hashes matched when timing fields were excluded. Cold HUD acquisition
over 23 identical frames improved 251.240 → 201.327 ms median with unchanged
recognition. These measurements are separate and must not be added together.
See `artifacts/performance-import-comparison.json`.

The validation runner now preserves immutable per-run frame/trace paths and
separate smoke/full results. Failed retries cannot truncate previous successful
evidence. Regression tests cover mode selection, failed retries, disappeared
cache files, and strict cached-only operation. Browser checks pass all thirteen
coverage/cross-car scenarios on desktop and mobile, including stale-result
labels, different-track rejection, swapping and unknown-track confirmation.
See `artifacts/expansion-browser/`; personal saved sessions were not modified.


## Two-Video Library Reset

The eight previous session analyses and duplicate cached downloads were removed.
Only `c0SJgXUahEM` (reference) and `x-RaRVP00r4` (current) were analyzed afresh,
using their already-downloaded video files. They contain 10,753 and 9,894
observations; analysis took 97.7 s and 145.32 s respectively (3.67x and 2.27x
the video duration). These are local processing figures, not live stream latency.

The first video's longest driving segment extends past its finish and includes
more than one lap; it has no readable standard lap clock. The second includes
about 15 seconds of lead-in. Defaulting to those segments was not a valid lap
comparison. Both now have persistently saved, manually reviewed boundaries:

| Video | Source IN / OUT | Selected lap duration |
| --- | --- | --- |
| c0SJgXUahEM | 1.958333 / 118.875 s | 116.916667 s |
| x-RaRVP00r4 | 14.725 / 132.175 s | 117.450 s |

Each boundary is the midpoint between native frames where the same painted
stripe is last visible and disappears under the central bonnet edge. No duration
was forced from a title or result. The durations independently agree with the
displayed 116.915 s and 117.450 s results. These are visible-car reference planes,
not the hidden game timing plane. Allow at least 0.033 s frame uncertainty and
about 0.1 s cross-camera systematic uncertainty. The second video's official
HUD clock crosses around 0.075 s later; mixing these conventions would offset
the overlay. Evidence: `artifacts/*finish-stripe*.png` and
`artifacts/two-video-reviewed-ranges.json`.

`scripts/check_reviewed_laps.py` reads the saved reviewed defaults and verifies
that delta is available, starts at zero and ends at **+0.533333 s**. Endpoint
distance scaling is only 0.10534%; no speed gaps require interpolation in either
selected lap. Speed, pedals, gear and steering are observed at all plotted points;
coverage does not establish measurement accuracy.

Three interior holdouts were not used to fit the registration:

| Physical marker | Position residual | Time residual magnitude | Visual uncertainty |
| --- | --- | --- | --- |
| Hamilton painted checker stripe | 3.945 m | 67 ms | 100 ms |
| Hangar bridge abutment | 4.003 m | 63 ms | 250 ms |
| Wellington bridge abutment | 1.567 m | 25 ms | 250 ms |

The bridge checks are coarser because camera/FOV edges determine visibility.
These checks support the overlay at inspected locations, not exact alignment
everywhere or lateral racing-line reconstruction. The videos also advertise
different ACC versions (v1.9 and v1.10); pace differences cannot all be attributed
to driving. Audit output: `artifacts/two-video-reviewed-audit.json`.

## Earlier Lap Alignment Checks

The previous delta gate rejected the entire supplied lap because three isolated
speed observations were missing. Those intervals span 0.066-0.067 seconds each;
the comparison now estimates their integrated movement, while retaining null raw
speed values and reporting the estimates. Long gaps and cuts still block delta.

Clock-based candidates are now placed at the estimated timer origin, not the
later confirmation frame. Re-evaluation of the existing complete own lap gives
147.6475-269.961 source seconds (122.3135 seconds); the original stored lap record
remains unchanged. This corrects algorithmic confirmation delay but is not a
certified physical timing-line measurement. Interrupted and partial laps are
excluded from these automatic comparison candidates.

`scripts/check_visual_laps.py` inspects the supplied videos read-only. The
measured broad searches found one reference finish candidate at 118.600 seconds
from a manually chosen 1.750-second anchor (9.737 seconds for a 98.25-second search
window), and one own candidate at 270.049 seconds from 147.727 (12.643 seconds for
a 107.273-second search window). A cross-view scene near own 210.023 seconds was
proposed near reference 61.150 seconds in 2.692 seconds. These are scene-recurrence
proposals, not independently validated exact timing correspondences. Sampling
resolution of 0.05 seconds is explicitly **not timing accuracy**; feature matches
can vary between runs and need physical-marker inspection.

The actual asynchronous HTTP workflow was also exercised on both recordings:
finish search, cross-view search and a three-anchor comparison completed. The
selected durations produced a consistent +5.4635-second finish delta. This tests
API integration and elapsed-time arithmetic, not physical alignment accuracy;
the candidate anchors were not saved as user-confirmed calibration. Both complete
saved-session hashes and the live source state remained unchanged. See
`artifacts/visual-laps-findings.md` and `artifacts/visual-laps-checks.json` for
footage evidence and the repeatable direct-video checks.

## Reference And Steering Update

Both full recordings were processed through `/api/imports` with their own HUD
profiles. The original saved sessions were not replaced. Reports:
`artifacts/reference-pipeline.json`, `artifacts/steering-pipeline.json`.

| Recording | Observations | Analysis + SQLite | Playback factor | Median / p95 local processing |
| --- | --- | --- | --- | --- |
| Supplied PS5, 545.6 s | 16,340 | 118.256 s | 4.61x | 6.799 / 8.285 ms |
| Linked YouTube, 358.4 s local copy | 10,753 | 91.966 s | 3.90x | 4.798 / 6.265 ms |

The linked file is 1080p60, sampled at 30 Hz. The automatic detector found its
German instrument cluster at approximately 0.882x the sample profile scale,
with a shifted location. Acquisition/search frames occasionally take about
120-200 ms; steady-state ROI extraction is substantially faster. These figures
are throughput, not guaranteed per-frame deadlines. Reports precede the final
clock-fallback and bounded-memory refinements; channel/lifecycle regressions
were rerun after those changes.

Conditional non-null channel coverage with a visible HUD: pedals/gear 100% in
both runs; steering 100% in the supplied video and 99.88% in the reference;
speed 99.94% and 99.89% respectively. This is **coverage, not ground-truth
accuracy**. Steering is signed HUD-axis percentage, not physical wheel angle.
The reference's normal lap clock and RPM were not readable by these templates.
Visual crossing proposals now assist manual range selection; its two driving segments include
onboard and external replay footage, with menus/setup footage excluded.

The full actual YouTube URL workflow also passed, not just a mocked downloader:
queued -> downloading -> analyzing -> completed, **103.09 seconds including
download**, 10,753 saved observations. `scripts/check_youtube_import.py` reproduces
the opt-in network test using an isolated database. Details are in
`artifacts/youtube-import-check.json` and `artifacts/youtube-check.sqlite3`.
No browser cookies, sign-in or content-restriction bypass was used.

Gear quality audit found no gear runs shorter than 0.12 seconds in either entire
recording, and no non-adjacent gear jumps in the user's complete lap. Its 46
observed shifts include visually checked 2->3->2 and 1->2->1 sequences. This
checks for flicker; it does not certify every frame against raw game telemetry.

## Initial Baseline

The reproducible full-video benchmark is
[`artifacts/benchmark-full.json`](artifacts/benchmark-full.json):

| Measurement | Result |
| --- | --- |
| Frames decoded and extracted | 16,340 / 16,340 |
| Source duration analyzed | 545.595 s |
| Wall time | 66.700 s |
| Throughput | 244.98 frames/s, 8.18x playback |
| Extractor median | 4.077 ms |
| Extractor 95th percentile | 5.227 ms |
| Decode median | 0.108 ms |
| BGR conversion median | 0.353 ms |

This benchmark includes local file decode, conversion, extraction and analysis.
It excludes preview encoding, persistence, browser rendering and network/stream
delay. It was collected before the final additional timer plausibility guard;
that guard adds only scalar comparisons, but these figures are the measured run,
not a guarantee for other machines. The separate actual-server full-pipeline
check is recorded in `artifacts/full-pipeline.json` and includes SQLite and previews:
**all 16,340 frames saved in 102.214 seconds (5.34x playback)**. Its per-frame
extraction/analysis median is 6.146 ms and p95 is 7.423 ms. The completed session
contains five HUD gaps, one complete lap, one interrupted invalid lap, and
82 rejected clock outliers. End-to-end throughput includes the other server work;
the per-frame timing is not a PS5-to-phone latency measurement.

Coverage conditional on a recognized HUD: pedals and gear 100%, speed 99.94%,
RPM 23.27%, raw lap clock 72.66%. Coverage is **not accuracy**. Template readings
can be wrong; the raw red-clock audit exposed ambiguities that the subsequent
temporal guard suppresses rather than interpolates. Per-channel missing values
and raw timer diagnostics are retained in session JSON.

Identified absent-HUD intervals approximately 0.20-8.64, 10.68-21.19,
350.52-384.99, 536.00-537.37, and 540.47-545.60 seconds, relative to the first
frame. The video has three visible timer resets. One full continuous lap is
about 122.355 seconds; the later 239.406-second interval is interrupted and invalid.
These are video-derived boundary estimates, not millisecond-certified lap times.
The figures in this older full-run report describe the stored lap boundaries;
the newer read-only timer refinement is documented above.

## Browser Checks

For the current two-video library, `scripts/check_two_video_browser.py` passed:
exactly two completed recordings, reviewed defaults selected automatically,
working +0.533333 s endpoint delta, no plotted samples beyond either finish,
both lap colors in all five channels, paired source frames, single-recording
reload/reselection, and 360/390 px mobile layouts. The inspector shows actual
decoded PTS separately from the selected timestamp (request 118.875 s displayed
decoded 118.866667 s). Screenshots were inspected without overlap or overflow.
`scripts/check_reviewed_defaults_browser.py` additionally tests obsolete session
IDs, revised defaults, retained manual choices, mismatched marker rejection and
unverified range saving with isolated browser fixtures. Both suites report zero
page errors. The single-recording delayed-response suite passed again against
the new IDs, with saved-recording hashes unchanged. Replay EOF/Stop/seeking and
phone pedal-isolation fixtures were also rerun successfully.

`scripts/check_browser.py` against the real localhost server passed:

- Recording selection, analysis start, seeking, pause/resume and stop.
- HUD regions, actual preview image, profile save and QR/phone address selection.
- Saved-session loading, replay, analysis and matching original-time video preview.
- Desktop layouts at 1440, 1280, 768 and 390 px wide.
- Phone layouts at 740x360, 640x360, 360x740 and 412x915.
- Nonblank phone canvas pixels, stale input clearing and no page JavaScript errors.

Screenshots were inspected in `artifacts/`; the phone view has no overflowing
content at tested sizes. No physical Samsung device was attached.

The reference workspace was additionally checked against both actual saved
recordings: independent selectors, custom IN/OUT ranges, synchronized channel
cursor, source thumbnails, full-frame range inspector, zoom/reset, import dialog,
saved selections after reload, gated delta and desktop/tablet/mobile layouts.
The comparison browser checks use existing sessions and do not replace or stop
the user's source. See `scripts/check_comparison_browser.py` and its reports in
`artifacts/` for the final reproducible checks.

`scripts/check_registered_comparison_browser.py` verifies actual endpoint delta,
piecewise landmark correspondence, exact source-time editing, persisted settings,
and separate cyan-solid/amber-dashed named lap traces. Both colors are nonblank
in every channel. Finish and cross-view candidate UI workflows use isolated
response fixtures and require explicit inspection/acceptance; the separate
direct-video and HTTP checks above exercise real recognition. Desktop and
360/390 px mobile layouts pass without overflow or JavaScript errors. The older
comparison and track-review scripts were updated and passed again on real saved
recordings, including precise frame requests and unchanged session hashes.
Changing an IN marker or landmark timestamp during a pending visual search is
also verified to cancel the obsolete job and re-enable its controls.

`scripts/check_single_recording_browser.py` checks the streamlined Analysis view:
the four top statistics and bottom braking-events table are absent. Clearing
either recording removes its trace, legend, readouts and source frames, plus the
delta and pair-registration controls. Both axes, selected-range preservation,
single-source map review, reload persistence, reselection and delayed successful
or failed responses pass. All five current-only channels contain cyan trace
pixels and no amber trace pixels; reference-only reverses that result. Desktop
and mobile screenshots were inspected, with no overflow or JavaScript errors.
Saved-recording hashes and the live source state remain unchanged. Results are
in `artifacts/single-recording-browser-checks.json`.

`scripts/check_pedal_replay_browser.py` uses a temporary static server with
intercepted API/WebSocket fixtures, without access to the user's database. It
checks natural EOF, reopening an ended source and explicit Stop; each opens one
saved session without changing its ID. Timeline seeking, play-from-end restart,
background chart seeking and original source timestamps after a prior backwards
seek are covered. Direct red/green trace taps and accessible legend controls
isolate either pedal and restore both, with canvas-pixel checks at desktop,
390 px mobile, and 640x360/360x740 phone sizes. No JavaScript errors were observed.
The dedicated 0-30% release plot was removed from both displays.

`scripts/check_track_review_browser.py` passed against the two actual saved
recordings. It checks all six nonblank track canvases, origin/confirmation
gating, map and keyboard seeks, paired source-image timestamps and image pixels,
persisted settings, confirmation reset after range changes, elapsed-axis
suppression, and the full-frame inspector. Desktop 1440 px and mobile 390 px
screenshots were inspected: no overflowing or overlapping controls and no page
JavaScript errors. Only read-only comparison POSTs were sent; both saved-session
SHA256 hashes and the live source state were unchanged. See
`artifacts/track-review-browser-checks.json` and `track-review-{1440,390}.png`.

The supplied footage does **not** validate a racing-line measurement. Ten
widely separated minimap frames have a near-fixed red marker (about 0.4 px total
movement on each axis) over a scrolling centerline. The 14-16 px marker obscures
a 2-3 px centerline, with no rendered road boundaries or metric scale. The
reference has no minimap. `artifacts/racing-line-feasibility.md` and its image
audit document this limitation. The track view is nominal centerline progress
from integrated HUD speed, never lateral position, track usage or apex error.

`scripts/check_capture.py` simulates a browser capture stream with actual HUD
pixels and exercises JPEG creation, upload, extraction, display, pause/resume and
track cleanup. It measured brake 99.3%, throttle 0%, speed 204 km/h from the
selected re-encoded frame. This is an integration check, not a claim about
physical capture hardware, video-to-video accuracy, or a calibration standard.

The earlier actual-server full-recording check processes and saves every source frame,
checks persistence and discontinuities, and leaves the analyzed sample available
in the application's session library. Those older sessions and development
database archives were removed during the two-video reset above.

## Recording Management

Deletion tests use temporary databases and generated video fixtures. They cover
confirmation eligibility, default video retention, explicit unshared managed-file
removal, original/shared/symlink protection, active-worker rejection, cascaded
records, queued observations, completed import links, and deleted-frame 404s.
Concurrent decoding finishes before a requested video removal proceeds.

`scripts/check_delete_swap_browser.py` passes isolated browser checks with
intercepted API/WebSocket traffic. Coverage includes swap ranges, segments,
landmarks and delta sign; persistence; one-sided swap; delete cancellation,
failure and pending states; replay and chart cleanup; deletion before opening
Analysis; and late comparison, frame, status and session-load responses.
No JavaScript errors or unexpected requests occurred. Results and desktop/mobile
screenshots are in `artifacts/delete-swap-review/`.

A read-only browser check swapped the two reviewed Silverstone recordings and
verified the finish delta changed from +0.533333 s to -0.533333 s, with both saved
range selections preserved. Their saved-data hashes were unchanged. Desktop
1440 px and mobile 390 px screenshots were inspected without overlapping controls
or page overflow. All five recordings present during this check were retained.

## Review Audio

Eighteen backend tests cover on-demand AAC preparation and cache reuse, nonzero
source timestamps, byte-range/HEAD playback, source/path/URL validation, missing
sound, failed preparations, localhost permissions and deletion during encoding.
The YouTube fallback is stubbed in unit tests and never downloads their fixtures.

`scripts/check_review_audio_browser.py` passes eleven isolated scenarios using
real WAV decoding/playback. Desktop 1440 px and mobile 390 px checks cover silent
defaults, mute/unmute, pause/seek/source rewinds, EOF/restart, view/visibility/live
transitions, failures/retries, stale preparation responses and deletion cleanup.
Held pre-seek and pre-restart thumbnails cannot replace the newly selected frame.
Screenshots fit without transport overflow; no JavaScript errors occurred.

Actual local `acc_video.mp4` audio was cached in about 5 seconds, and the saved
Fri3dolf reference's missing audio was downloaded and cached in about 7 seconds.
Both returned working 206 byte-range responses and played the resulting AAC in
Chromium at the selected source time. Saved telemetry hashes were unchanged.
These timings are observations on this machine/network, not performance promises.

## Not Verified

- A real Twitch live/VOD connection: no channel URL was supplied. Resolution,
  subprocess launch and stream-error behavior are implemented; access, ads,
  account restrictions and Internet delivery require testing on the target feed.
- Physical HDMI capture hardware, PS5 Remote Play with the user's wheel,
  or actual Samsung browser performance and Wi-Fi reachability.
- PS5-to-phone end-to-end latency. The app labels local processing time and
  received-frame age separately; upstream broadcast latency is unknown.
- Accuracy against game telemetry or independent pedal/steering measurements,
  other cars/fonts/languages beyond the checked English/German ACC HUD variants,
  other compression settings, or multi-hour soak reliability.
- Hidden physics channels or native MoTeC `.ld` output. These are not implemented
  or implied by the video-derived measurements.
- Measured racing lines, track-edge clearance or apex deviation. Real-world
  centerline geometry is not the ACC track mesh; a manually confirmed origin
  does not turn estimated progress into measured vehicle position.

PyAV and the OpenCV wheels on this Mac both include FFmpeg AVFoundation classes,
which causes startup duplicate-class warnings. File decode and all tested paths
pass; the app uses browser device capture, not either library's AVFoundation
device-capture API. No suppression of those diagnostics was installed.
