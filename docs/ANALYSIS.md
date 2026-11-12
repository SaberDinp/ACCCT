# Understanding a recorded lap

ACCC Telemetry reads the visible ACC HUD in recorded gameplay. The desktop app
analyzes recordings locally and lets you review them against another lap. It does
not collect live game telemetry or use delayed broadcasts for live driving advice.

## Import and select

Use **Import recording** to add a video as **Your run** or **Reference lap**.
Local files work without Internet access. Supported public YouTube recordings,
Twitch VODs/clips, and direct video URLs are downloaded for local analysis.
Private, expired, restricted, or unavailable videos can fail to import; use an
accessible local recording in that case. The app does not request platform
passwords or bypass sign-in restrictions.

Each recording remains available through **Recordings** in Analysis. Select a
detected lap or define source-time start/end boundaries. A reference is optional
for reviewing one recording. Label each recording with its car, circuit, game
version, and conditions when known. Titles are discovery hints, not verified
identity. Known different circuit layouts cannot be compared.

No sample race videos or existing session libraries are shipped in the source
repository. Start with your own footage or a public recording you may download.

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
hidden, rounded, or compressed. Inspect lap start/end frames and choose matching
physical crossings when comparing precise sections. Custom ranges can be saved
without claiming they have been independently verified.

**Same-lap endpoints** registers distance between confirmed IN and OUT crossings.
**Paired track landmarks** adds corresponding source timestamps inside the lap.
Registration adjusts the comparison axis, not the recorded inputs or source
elapsed times. Braking patterns are never automatically matched because that
could erase the driving differences being studied. Large corrections are flagged.

**Find next crossing** looks for the selected IN scene later in the same video.
The landmark image-match control can suggest a matching scene in the reference.
Both are bounded local feature-matching searches, not proof of exact position.
Inspect each suggestion before accepting it; choosing an arbitrary frame does
not make it a start/finish line.

## Missing data and interrupted laps

Hidden HUDs, timestamp jumps, frozen footage, unreadable inputs, and stopped
driving break continuity. Menus create missing values instead of invented zero
inputs. Distance and braking events are not inferred across long gaps.

For comparison distance only, an isolated speed gap of at most 0.5 seconds may be
estimated when adjacent readings pass plausibility checks. Original speed stays
missing, and repaired intervals are disclosed. Cuts and longer gaps break delta
locally. An interrupted lap is not presented as a complete lap.

## Calibration and footage quality

Automatic calibration locates supported ACC instrument clusters, including current
and legacy overlays, at their visible scale and position. Units are standardized
to km/h when they can be read; ambiguous units suppress speed rather than corrupt
distance. The legacy overlay has no usable steering strip.

Use **Calibration** inside Analysis to inspect a recording frame, adjust HUD
regions, and set the layout or speed units. Keep the complete pedal fills,
visibility anchors, digits, and lap clock inside their regions. The profile is
saved in your local library. Different fonts, very small HUDs, hidden input bars,
or severe compression may require different source footage. Region editing cannot
recover invisible data or teach the digit reader an arbitrary new font.

The bundled [car/circuit catalog](../assets/catalog/acc.json) is a selection aid,
not a claim that every car, camera, HUD, and circuit combination has been validated.
The [track-data documentation](../assets/tracks/README.md) describes geometry,
provenance, attribution, and licenses.

## Exports and storage

CSV exports are ordinary tables with units, not proprietary MoTeC `.ld` files.
The comparison's **Export for AI** button downloads a bounded JSON summary of
the currently compared ranges. It does not call an AI service or send your data
anywhere. You choose whether to share the exported file with another tool.

Your library holds the session database, recordings, profiles, and local caches
separately from the application. Updating or replacing the app preserves it.
Before moving a library between computers, note that older sessions may reference
absolute video paths. See [library and backup instructions](../desktop/README.md#existing-recordings-and-settings).
