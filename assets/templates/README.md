# Sample HUD Templates

These small glyph and label images are derived from the user-supplied
`acc_video.mp4`, using the manually inspected labels in
`scripts/inspect_video.py`. There is no trained neural network, external OCR
service, or downloaded model. OpenCV compares thresholded, normalized image
glyphs against these templates.

The default profile automatically locates the car-independent current ACC HUD
or the older English overlay. The geometry is not tied to a McLaren dashboard.
Normalized coordinates and multiscale anchors handle resized and moved HUDs;
occluded or unrecognized layouts still need manual calibration or remain
unavailable. Dashboard-only footage is not supported merely because its car
appears in the catalog.

Automatic calibration searches for English Throttle/Brake, German Gas/Bremse,
or French Accélérateur/Frein label anchors, verifies candidate instrument positions with speed or
gear glyphs, and relocates the whole instrument group. The German anchor is
from the user's linked reference `c0SJgXUahEM`, at 37.333 seconds. Its pixels
`[1730:1769, 900:929]` are normalized to the English anchor's 44 x 33 shape.
No pedal values or driving patterns are learned from the reference.

The French anchor comes from `t0Rgya0suMw` at decoded time 89.9899 seconds.
Its native crop `(x=1733, y=898, width=36, height=27)` is normalized to 44 x 33.
The crop includes the fixed right portion of the longer Accélérateur caption
and the Frein caption, so existing instrument geometry and confidence checks
apply unchanged. `scripts/build_french_hud_anchor.py VIDEO` reproduces it.
`tests/fixtures/french_hud_labels.json` records held-out first/second-lap frames
and setup/menu negatives; native HUD crops allow the regression tests to run
without downloading the video. This source has no visible lap-clock overlay,
and no timer or RPM values are invented when their evidence is absent or weak.

The lap-clock label block is located independently because HUD groups can move
separately. Missing anchors remain unavailable and searches are throttled. Set
`auto_calibrate` false to use explicit profile ROIs without relocation.

Recovered channels:

- Brake and throttle: horizontal colored fill, including amber highlighting.
  The sample's 144-pixel strip implies approximately 0.7 percentage points per
  source pixel before compression/rescaling error.
- Speed: visible integer, standardized to km/h. Automatic mode reads the visible
  km/h or mph label, with a fine-text fallback for translucent overlays on busy
  dashboards. It remembers a credible unit within that calibrated layout and
  clears it when the instrument group relocates. An unrecognized unit does not
  silently become km/h. An explicit profile unit remains authoritative.
- Gear: the observed N and 1-6 glyphs. Reverse is not represented in this sample.
- RPM: conservative digit matches; ambiguous readings remain null.
- Lap clock: white or red HUD digits; a red clock sets `lap_valid` false.
- Brake/throttle highlight: observed amber pixels, not a direct physics signal.
- Steering: the small white marker above the input bars, expressed as -100 to
  +100 percent of its visible axis (left negative, right positive). This is an
  observed HUD index, not physical steering angle, road-wheel degrees, or a
  universally comparable input calibration. Ambiguous/missing markers are null.
  The legacy HUD has no steering strip and always returns null for steering.

`confidence` and per-channel scores are image-match heuristics, not calibrated
probabilities or telemetry accuracy guarantees. Hidden HUDs return null values.
There is no speed, steering, suspension, tire-load, position, or physics inference
beyond the visible HUD. Menu/freeze/lap/session handling belongs to the engine
and analysis modules.

Regenerate labeled templates:

```sh
.venv/bin/python scripts/inspect_video.py --build-templates acc_video.mp4
.venv/bin/python scripts/inspect_video.py --reference-templates data/references/c0SJgXUahEM/media.mp4
```

Validate extraction with `.venv/bin/python -m unittest tests.test_vision tests.test_french_hud`.
The speed/gear validation uses 23 additional manually inspected frames that
are not used as template sources. Pedal tests exercise zero, partial, full,
amber, text overlay, directions, and non-contiguous color; they do not establish
agreement with unavailable raw PS5 input telemetry.

## Additional Layout And Held-Out Sources

`scripts/build_hud_variants.py` reproduces `labels_legacy_en.png`,
`lap_labels_legacy.png`, and `unit_mph.png` from the linked old McLaren recording
`RMMk8BH0e3E` at 90 seconds. `unit_kmh.png` comes from the original sample at
40 seconds. These frames are excluded from the new manual legacy evaluation.
No speed, gear, pedal, or steering templates were trained on the multi-car
held-out videos.

`tests/fixtures/legacy_hud_labels.json` records twelve manually inspected driving
frames plus six external-camera/menu frames. `multicar_hud_labels.json` records
sixteen independently inspected frames across Porsche/Spa, Ferrari/Monza,
BMW/Mount Panorama, and Mercedes/Brands Hatch. These are frame-level checks,
not a claim that all laps, every car/track pair, or physical steering angles
have been validated. Pedal and steering intervals describe visible pixel
extents, not inaccessible raw PS5 telemetry.

Run `scripts/check_hud_corpus.py` for a reproducible per-channel report and
cold/warm extraction timings. Missing local source videos are reported, never
counted as passes. The script reads media only and never opens the session DB.
