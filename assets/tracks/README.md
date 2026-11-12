# ACC track maps and annotations

The active map library covers the **25 ACC circuits**, using the ACC-specific
files from two pinned community datasets. `index.json` maps every catalog ID to
its original source file, SHA-256 checksum, source path and nominal length.
Both upstream datasets are bundled unchanged in their respective subdirectories.
The loader checks their checksums before using them. No runtime network access
or local ACC installation is required.

## Geometry: RaceIQ

- Source: https://github.com/SpeedHQ/RaceIQ
- Commit: `f1551541a01f2711ba01f4d1020babf78a2b90ff`
- Original files: `shared/data/tracks/acc/*-boundaries.json`
- Bundled files: `raceiq/*-boundaries.json`
- Attribution: SpeedHQ / RaceIQ contributors; ACC track geometry extracted from
  the game's `fastlane.ai` data.
- License: GNU AGPL-3.0, preserved in `LICENSE-RaceIQ-AGPL-3.0.txt`.
- Upstream extraction documentation: `RaceIQ-ACC-README.md`.
- Original extraction implementation:
  https://github.com/SpeedHQ/RaceIQ/blob/f1551541a01f2711ba01f4d1020babf78a2b90ff/server/games/acc/extract-tracks.ts

`telemetry/tracks.py` computes each paired left/right boundary midpoint, closes
its short final gap, preserves the aspect ratio and scales distance uniformly
to the nominal circuit length. Source node order and source lap origin are
preserved. Source bounds are derived from ACC fastlane data and widths, with
upstream fallback widths; these are approximate map outlines, not a surveyed
mesh or a measured vehicle trajectory. Widths and AI racing lines are not
exposed as driving recommendations. The upstream files named `centerline.csv`
were deliberately not used: some contain an AI racing line pending migration.

### Coordinate handedness

The bundled boundaries use raw ACC `x,z` coordinates, **not Cartesian map
`x,y` coordinates**. Raw `x` maps to screen right and raw `z` maps to screen
down. Inverting `z` as though it were Cartesian north/up mirrors the circuit:
right-hand corners appear left-handed even though the lap order is unchanged.
`validate_geometry(..., coordinate_system="acc_xz")` now applies the ACC
convention explicitly. Its default remains Cartesian `x,y` for other callers.

This agrees with the upstream rendering pipeline. Its
[coordinate utilities](https://github.com/SpeedHQ/RaceIQ/blob/f1551541a01f2711ba01f4d1020babf78a2b90ff/shared/racing/tracks/coords.ts)
negate raw ACC `x` before drawing;
[the map renderer](https://github.com/SpeedHQ/RaceIQ/blob/f1551541a01f2711ba01f4d1020babf78a2b90ff/client/src/components/analyse/track-map/static-drawing.ts)
then mirrors that intermediate `x` again and sends `z` downward. The net
transformation of the original bundled data is therefore increasing `x` to
the right and increasing `z` downward, without a reflection between those axes.

Regression checks follow source traversal through known real corners, using
the sign of the displayed path's turn. [Monza's circuit operator describes
Rettifilo as right then left](https://www.monzanet.it/tracciato-pista-monza/).
[Silverstone identifies Copse as a right-hander](https://www.silverstone.co.uk/news/silverstones-guide-grandstands)
and [Farm as a left-hander](https://www.silverstone.co.uk/news/lap-through-ages-silverstone-celebrates-75-years-memorable-moments).
All four must retain those directions on screen. Tests also verify that the
correction preserves every source point's along-lap distance on all 25 maps;
lap order, distance indexing, timing/corner fractions and saved recording
origins do not change.

Geometry lengths are also returned separately from nominal lengths. This
map-only distance parameterization does not stretch video time, measured speed,
lap duration, or comparison data. Boundary midpoints do not claim to reproduce
the official survey distance to metre precision.

The source extractor explicitly maps ACC's game track IDs to these files.
Barcelona retains the pre-2021 hairpin and final chicane; Zandvoort is ACC's
pre-2020 layout. Indianapolis is ACC's 15-turn GT road course, not the IndyCar
configuration. `nordschleife-boundaries.json` maps to `nurburgring_24h` and is the
combined GP/Nordschleife circuit: its midpoint outline measures about 25.137 km,
with a 25.378 km nominal lap. It is not the standalone Nordschleife. Silverstone
uses ACC's National pit start before Copse on the full Arena circuit. Source
layout IDs, outlines, measured lengths and annotation ordering were reviewed.

## Annotations: Lovely Track Data

- Source: https://github.com/Lovely-Sim-Racing/lovely-track-data
- Commit: `eb4af9572aeb247e6406bc154d73a3bd16d8c28a`
- Original files: `data/assettocorsacompetizione/*.json`
- Bundled files: `lovely/*.json`
- Attribution: © 2025 Lovely Sim Racing, https://lsr.gg. Upstream credits
  https://www.racingcircuits.info for corner and straight naming.
- License: CC BY-NC-SA 4.0,
  https://creativecommons.org/licenses/by-nc-sa/4.0/ . Its attribution,
  noncommercial and share-alike terms apply to these annotation data.
- The upstream license statement and format documentation are preserved in
  `Lovely-README.md`; full terms in `LICENSE-Lovely-CC-BY-NC-SA-4.0.txt`.

Normalized ACC lap fractions locate corner names, named straights and the two
sector boundaries on the source-ordered map. Start/finish is shown at the ACC
source lap origin, separate from the two sector splits. These are community
map estimates: the source fractions are rounded and their projection onto an
approximate midpoint outline is not a surveyed timing-line coordinate. Every
annotation exposes `position_kind`; `start_finish_verified` remains false.

Corner numbering and supplemental circuit names, where available, are in
`corner-numbering.json`, with per-track or per-item source URLs. Its `turns`
array refers to original Lovely row order, **not** the sorted/filtered API
array. Complexes retain a number range at their single sourced position instead
of creating fictitious individual apexes. Original source rows are retained as
`source_index`; extra sourced annotations are separate. Named straight positions
are interval midpoints, not entire ranges being claimed to be perfectly straight.

Five equivalent-layout Lovely records in `lovely-name-sources/` supplement names
for Bathurst, Oulton Park, Snetterton, Zandvoort and Zolder while retaining ACC
marker positions. `name-source-index.json` records their checksums and source
paths at the same pinned commit. The same Lovely attribution and CC BY-NC-SA
4.0 terms apply to these snapshots and the derived name corrections. Numbering
corrections also cite circuit operators and race-organizer maps in
`corner-numbering.json`. A generic start/finish-straight label is a functional
description, not an asserted official proper name. The Nordschleife retains
named sections where the source does not establish individual corner numbers.

The loader makes conservative, disclosed source-data repairs. A missing apex or
an apex outside its own supplied turn interval uses the interval midpoint and
is explicitly marked as an estimate. Exact duplicate source corner rows are
collapsed; a corner without a usable source position is omitted unless a sourced
supplement supplies one. Straight intervals longer than 40% of a lap are
rejected (the original Brands Hatch Brabham interval `[0, 0.6]` is an example).
The resulting `annotation_warnings` disclose these repairs/omissions. Raw files
remain unchanged so each transformation can be audited.

## Recording alignment remains separate

A map's start/finish reference does not identify the first frame of an arbitrary
recording. `requires_origin_confirmation` remains true: the user confirms the
recording's map origin and direction before video distance is displayed. Map
annotations always retain source-order fractions and do not shift when the
recording's alignment changes. They are geographic references, not detections
of where a particular video car actually crossed a corner or timing line.

## Legacy TUMFTM snapshots

The top-level CSV files are retained as historical source assets; they are no
longer the active ACC map library. They are unmodified TUMFTM racetrack database
files, commit `e59595d1f3573b30d1ded6a08984935b957688e0`, licensed LGPL-3.0:
https://github.com/TUMFTM/racetrack-database/tree/e59595d1f3573b30d1ded6a08984935b957688e0 .
Original notices remain in `LICENSE-TUMFTM.txt`, `README-TUMFTM.md` and
`COPYING-GPL-3.0.txt`. Attribution: TUMFTM, Technical University of Munich, and
OpenStreetMap contributors, https://www.openstreetmap.org/copyright .
