# Developing ACCC Telemetry

Most users should install an app from the [download table](../README.md#download-the-app).
This guide is for working on the analyzer, UI, and desktop packaging.

## Run from source

Use Python 3.14 for the same runtime as the desktop builds. Dependencies include
PyAV video decoding, so a separate FFmpeg installation is not required.

```sh
git clone https://github.com/SaberDinp/ACCCT.git
cd ACCCT
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --host 127.0.0.1 --port 8000
```

On Windows:

```powershell
git clone https://github.com/SaberDinp/ACCCT.git
cd ACCCT
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000**, leave the terminal running, and import a recording.
Stop with **Ctrl+C**. `launch.py` starts the same application. Use `--port 8001` if
8000 is occupied. The shared VS Code task **Start ACCC Telemetry** uses `.venv`.

Recordings and generated data are intentionally absent from a clean checkout.
The app creates a local library when it starts. Do not add personal race footage,
session databases, credentials, cookies, or generated analysis reports to Git.

The desktop wrapper needs Node.js 24:

```sh
npm ci --prefix desktop
npm start --prefix desktop
```

It uses the project's `.venv` for development. See the
[desktop build guide](../desktop/README.md#build-from-source) for frozen runtimes,
architecture checks, installer formats, and environment overrides.

## Project map

| Path | Purpose |
| --- | --- |
| `main.py` | Local HTTP API and source launcher. |
| `telemetry/vision.py`, `calibration.py`, `steering.py` | HUD recognition and calibration. |
| `telemetry/imports.py` | Recorded video download and analysis jobs. |
| `telemetry/comparison.py`, `analysis.py` | Shared axes, laps, metrics, and continuous-data rules. |
| `telemetry/lap_detection.py`, `visual_laps.py` | Lap boundary refinement and scene searches. |
| `telemetry/storage.py` | SQLite persistence. |
| `telemetry/tracks.py` | Circuit geometry and estimated map progress. |
| `web/` | Analysis workspace, credits, photos, and shared A-with-three-Cs logo. |
| `assets/` | Small HUD templates, catalog, track geometry, and source licenses. |
| `desktop/` | Electron shell, packaging, runtime builders, icons, and smoke checks. |
| `tests/`, `scripts/` | Automated tests and developer inspection tools. |
| `.github/workflows/` | Source checks, native package checks, and tagged release publishing. |
| `data/`, `artifacts/`, `dist/` | Local data, generated evidence, and builds; ignored by Git. |

Historical local `legacy/` and generated `demo/` folders are also ignored. Older
demo-generation and investigation scripts may expect separately supplied footage
or generated fixtures. They are not part of installation or the app's UI.

## Test changes

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q
npm test --prefix desktop
```

Tests requiring a developer's private sample recording skip when it is absent.
The source CI also runs isolated browser checks for navigation/imports, the
Start/End/Lock workflow, circuit inference and manual overrides, comparison
export, responsive native-video layout, and missing HUD observations. They use
local generated fixtures and do not change a user's library. Legacy standalone
checks dedicated to the removed registration/editor interface have been retired;
backend coverage for registration and recording context remains in `tests/`.
Other scripts under `scripts/check_*_browser.py` can require a running source
server or local recordings; inspect their arguments before running them. The packaged smoke check creates an isolated
temporary library, opens the actual app twice, and checks resource loading,
recording persistence, export, and shutdown. Set `ACCC_SMOKE_VIDEO` to exercise a
specific local recording without shipping it in the repository.

CI generates a tiny video from the attributed HUD fixture using
`desktop/create_smoke_video.py`; this gives every platform the same redistributable
input and checks importing both your run and a reference. Generate it with:

```sh
.venv/bin/python desktop/create_smoke_video.py artifacts/desktop-package/sample.mp4
```

## Extraction and validation work

The extractor uses OpenCV color segmentation, template digit recognition, and
local feature matching. No cloud model or model download is needed. Changes to
HUD recognition should be checked against both ordinary footage and gaps,
fades, ambiguous digits, unit labels, and hidden inputs. Preserve missing values
when confidence is insufficient.

Useful commands with your own local media:

```sh
.venv/bin/python scripts/benchmark.py /path/to/recording.mp4 --seconds 60
.venv/bin/python scripts/inspect_video.py /path/to/recording.mp4
.venv/bin/python scripts/validate_corpus.py report
```

Template-generation tools contain manually labeled frame choices; review these
before regenerating shipped assets. The validation corpus and reports live under
ignored `data/validation/` and `artifacts/`. Sparse checks, full-video extraction,
and independent lap review measure different things. A catalog entry or successful
download is not proof of accurate telemetry for that car/HUD combination.

## Public project page

The public download and introduction page lives in [`index.html`](index.html)
and is hosted at **https://saberdinp.github.io/ACCCT/**. GitHub Pages publishes
the `docs/` directory from `main`; `.nojekyll` keeps it a plain static site with
no build dependencies. The desktop app still runs locally on the user's computer.

Keep its feature descriptions consistent with this repository's README. Download
buttons link to the latest GitHub release so the page does not pin old installer
filenames. When changing the public URL, update the canonical URL, social-preview
metadata, structured data, and `sitemap.xml` together.

The hexadecimal `.txt` file in this directory is a public IndexNow ownership
verification file. Keep it available when notifying participating search engines
about this page. Submission requests use the full file URL as `keyLocation`
because the project is hosted beneath `/ACCCT/`. Search engines decide when to
crawl and index the page; a submission is not a ranking or indexing guarantee.

## Releases

Update `desktop/package.json`, its lockfile, the README download filenames, and
the release notes together. Run source checks and inspect the UI before creating
a version tag such as `v1.4.0` matching the desktop package version.

The release workflow builds and exercises all five targets on native runners:
Apple Silicon Mac, Intel Mac, Windows x64, Linux x64, and Linux ARM64. It only
publishes after all package jobs succeed and the complete set of 14 expected
installers/archives is present. The upload includes `SHA256SUMS.txt` and
`INSTALL.txt`; source archives are not substitutes for those installers.

A published release is not overwritten by a workflow retry. Fixes to published
app code get a new version and tag. Mac bundles use ad-hoc signing without a
certificate for bundle integrity; CI checks that signature before launching.
Apple Developer ID signing, notarization, and Windows publisher signing require
project-owned credentials; never commit them. A runner smoke test does not certify
every OS release, Linux distribution, or GPU. Record its actual test scope in the
release notes.

Build outputs and downloaded runtimes stay ignored. The repository should contain
only source and required small assets; installer binaries belong in Releases.
