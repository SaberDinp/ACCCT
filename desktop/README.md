# ACCC Telemetry desktop apps

**ACCC Telemetry** stands for **ACC Console Telemetry**. The desktop apps open the
recorded-lap analysis UI in their own window and include Python, video decoding,
analysis, and download helpers. End users do not need Python, Node.js, or a
terminal to launch an installed app. Local recordings work offline; importing
public recorded videos requires Internet access. No account is required.
The app does not provide live monitoring or stream telemetry to a phone.

## Choose a package

Download from the [README download table](../README.md#download-the-app) or
[GitHub Releases](https://github.com/SaberDinp/ACCCT/releases/latest). Choose an
installer, not GitHub's **Source code** archives. Local builds write version
**1.3.0** packages to `dist/`. Match both the operating system and CPU:

| Computer | Release target | Package formats |
| --- | --- | --- |
| Apple Silicon Mac (M-series), macOS 14+ | `mac-arm64` | `.dmg`, `.zip` |
| Intel Mac, macOS 13+ | `mac-x64` | `.dmg`, `.zip` |
| Windows 10/11, 64-bit Intel/AMD | `win-x64` | setup `.exe`, portable `.zip` |
| Linux desktop, 64-bit Intel/AMD | `linux-x64` | `.AppImage`, `.deb`, `.rpm`, `.tar.gz` |
| Linux desktop, 64-bit ARM/AArch64 | `linux-arm64` | `.AppImage`, `.deb`, `.rpm`, `.tar.gz` |

Mac and Windows filenames follow `ACCC-Telemetry-1.3.0-<target>.<extension>`.
Linux package formats use different spellings for the same CPU architecture.
Every filename begins with `ACCC-Telemetry-1.3.0-`, followed by:

| Format | Intel/AMD 64-bit suffix | ARM64 suffix |
| --- | --- | --- |
| AppImage | `linux-x86_64.AppImage` | `linux-arm64.AppImage` |
| DEB | `linux-amd64.deb` | `linux-arm64.deb` |
| RPM | `linux-x86_64.rpm` | `linux-aarch64.rpm` |
| TAR.GZ | `linux-x64.tar.gz` | `linux-arm64.tar.gz` |

On a Mac,
**Apple menu > About This Mac** shows either an Apple chip or an Intel processor.
On Linux, `uname -m` reports `x86_64` for the x64 package and `aarch64` for ARM64.

The Intel package adds support for Intel MacBooks; it does not add support for
macOS 12 or earlier. PowerPC Macs, 32-bit Intel/ARM computers, native Windows ARM,
and Alpine/musl Linux are not release targets. The Python dependencies require
macOS 14 on Apple Silicon even though the desktop framework also supports Intel
macOS 13. See [Electron's platform support](https://github.com/electron/electron/blob/v45.3.1/README.md#platform-support)
for the underlying desktop framework's operating-system requirements.

## Install and launch

### macOS

Open the matching DMG, drag **ACCC Telemetry** to **Applications**, then open it
from Applications, Spotlight, or the Dock. The ZIP contains the same `.app`.
Keep the application bundle intact.

### Windows

Double-click `ACCC-Telemetry-1.3.0-win-x64.exe`. The one-click installer installs
for your user, creates Start menu and Desktop shortcuts, and opens the app when
finished. You do not need administrator access for this installation.

For the portable ZIP, right-click it and choose **Extract All**, then open
**ACCC Telemetry.exe** inside the extracted folder. Do not open it from inside
the ZIP; keep its `resources` folder and the other extracted files beside it.

Mac builds use ad-hoc signing for bundle integrity, without Apple Developer ID
signing or notarization. This does not establish a verified publisher or remove
Gatekeeper's first-launch checks. Windows builds are unsigned. First launch can
therefore show a publisher warning. macOS may offer
**System Settings > Privacy & Security > Open Anyway** after a blocked launch;
Windows may offer **More info > Run anyway**. Use these only for a package whose
source you trust. Publisher signing/notarization is a separate release step.

### Linux

The package families target these common desktop distributions; they have not
all been tested individually:

| Distribution family | Preferred package |
| --- | --- |
| Ubuntu 22.04/24.04, Debian 12+, Linux Mint 21/22 | `.deb` |
| Supported Fedora and openSUSE desktop releases | `.rpm` |
| Other compatible glibc-based desktops | `.AppImage` or `.tar.gz` |

For a package-manager installation, open the DEB/RPM in your software installer,
or use these Intel/AMD examples from the download folder. On ARM64, use
`linux-arm64.deb` or `linux-aarch64.rpm` instead:

```sh
# Ubuntu, Debian, Linux Mint
sudo apt install ./ACCC-Telemetry-1.3.0-linux-amd64.deb

# Fedora
sudo dnf install ./ACCC-Telemetry-1.3.0-linux-x86_64.rpm

# openSUSE
sudo zypper install ./ACCC-Telemetry-1.3.0-linux-x86_64.rpm
```

Then choose **ACCC Telemetry** in the applications menu. DEB and RPM installation
resolves the system dependencies declared by the package.

For AppImage, mark the file executable in its Properties dialog and open it.
The terminal equivalent is:

```sh
chmod +x ACCC-Telemetry-1.3.0-linux-x86_64.AppImage
./ACCC-Telemetry-1.3.0-linux-x86_64.AppImage
```

For ARM64, use `ACCC-Telemetry-1.3.0-linux-arm64.AppImage` instead. AppImage may
require FUSE 2 compatibility libraries (`libfuse2` on Ubuntu 22.04,
`libfuse2t64` on Ubuntu 24.04). Keep any existing FUSE 3 installation. See the
[AppImage FUSE instructions](https://docs.appimage.org/user-guide/troubleshooting/fuse.html).
The TAR.GZ alternative can be extracted to a writable folder; open
`accc-telemetry` and keep all extracted files together.

Linux requires a graphical desktop, **glibc 2.28+**, and a libstdc++ providing
**GLIBCXX 3.4.22+**, plus the GTK 3, NSS, accessibility, graphics, and audio
libraries used by Electron. The builders audit the Linux bundles for
these GLIBC/GLIBCXX symbol floors. That checks binary requirements; it does not
certify every older distribution or desktop configuration. Use a supported
distribution from the families above. The Chromium sandbox remains enabled; the
app does not add `--no-sandbox`. Prefer DEB/RPM if your system restricts
unprivileged user namespaces or an AppImage cannot start; the installers configure
the supported sandbox setup. Do not disable the sandbox to work around launch issues.

### Updating from an older version

Close the older running app, install the matching **1.3.0** package, and reopen
**ACCC Telemetry**. An older version can retain a cached Monitor interface after
an update. Use **View > Force Reload** to refresh it: **Command+Shift+R** on macOS
or **Ctrl+Shift+R** on Windows/Linux. Version 1.2.4 and later clear the HTTP cache
automatically before loading the current Analysis and Credits UI on each launch.
This clears cached web responses only; your recordings, saved sessions,
calibration, and app preferences are preserved.

## Existing recordings and settings

On the first launch under the new name, the app checks the previous
**Apex Telemetry** settings. If it finds that library, it keeps using it in place;
recordings are not copied, renamed, or deleted. This preserves the absolute video
paths in existing sessions. The renamed desktop app uses a new browser profile;
its comparison selections may need to be chosen again. When the original browser
profile is retained (for example, running from source at the same address),
legacy comparison and track-map keys are still read. Close the old app before
opening the renamed app with the same library.

For a library from this source project, choose **File > Open Existing Library…**
and select the project/library folder containing `data/telemetry.sqlite3`.
**File > Show Library Folder** opens the active location. **File > Use Default
Library** switches back to the new app's default library without moving data.

Fresh installations use:

| Operating system | Default library |
| --- | --- |
| macOS | `~/Library/Application Support/ACCC Telemetry/library` |
| Windows | `%APPDATA%\ACCC Telemetry\library` |
| Linux | `$XDG_CONFIG_HOME/ACCC Telemetry/library`, normally `~/.config/ACCC Telemetry/library` |

Each library contains `data/telemetry.sqlite3`, uploads, downloaded recordings,
calibration settings, and audio caches. Replacing the installed app preserves
this separate library; the Windows uninstaller preserves it too. Back up the
complete library while the app is closed. Moving a library to another computer
can require reimporting recordings referenced by absolute paths.

## Analysis, credits, and exports

The main tabs are **Analysis** and **Credits**. Import a recorded video as your
run or a reference lap through **Video options**, then compare their visible
inputs. Pick each **Start of lap** and **End of lap** with **Lock**; HUD detection
and map progress work automatically. Recorded YouTube/Twitch links are
downloaded before analysis; live streams and capture devices are not app sources.

Exports open a Save dialog and use the `accc-` filename prefix. Comparison JSON
identifies itself as `accc-telemetry-comparison`. Exporting does not send your
recordings or telemetry to an AI service.

Credits introduces Rice Racing, shows the real team, and explains how to give
through Rice University. The website and donation links open in your normal
browser. Donations are optional and no payment details enter the app.

Closing the desktop window stops the analysis engine. Opening the app again
focuses an already-running instance. **Help > Show Logs** opens startup logs.

## If something does not open

| Symptom | First step |
| --- | --- |
| Wrong Mac download | Check **Apple menu > About This Mac**, then download Apple Silicon or Intel accordingly. |
| macOS blocks a first launch | Open the downloaded app once, then check **Privacy & Security** for **Open Anyway**. Only approve a build from a source you trust. |
| Windows shows an unknown publisher | Confirm the file came from this repository; if offered, choose **More info > Run anyway**. |
| Windows portable app cannot find resources | Use **Extract All** and keep the entire extracted folder together. |
| Linux DEB/RPM will not install from a file manager | Run the matching package-manager command above; it can explain a missing system dependency. |
| AppImage will not launch | Prefer the DEB/RPM package for your distribution; check executable permission and the FUSE requirements above. |
| App opens with no recordings | A fresh library is empty. Use **Import recording**, or **File > Open Existing Library** for an older library. |
| Old Monitor UI appears after an update | Upgrade to **1.3.0** for the automatic fix. In an older version, use **View > Force Reload**: **Command+Shift+R** on macOS or **Ctrl+Shift+R** on Windows/Linux. |
| App cannot start its analyzer | Wait for the initial launch to finish, then inspect **Help > Show Logs** and include the app/OS version in an issue. |

## Build from source

Build tools need Node.js 24 and Python 3.14.10. The finished apps include their
runtimes. Commands below run from the project root and use a separate build
virtual environment; on Windows, use `.venv-build\Scripts\python.exe` in place
of `.venv-build/bin/python`.

```sh
python3 -m venv .venv-build
npm ci --prefix desktop
```

Generate the small redistributable smoke video before UI checks. This uses the
attributed HUD test fixture, not a private recording:

```sh
.venv-build/bin/python desktop/create_smoke_video.py artifacts/desktop-package/sample.mp4
export ACCC_SMOKE_VIDEO=artifacts/desktop-package/sample.mp4
```

Run this after installing the platform's Python dependencies below. In PowerShell,
set `$env:ACCC_SMOKE_VIDEO = "artifacts/desktop-package/sample.mp4"` instead of
`export`. The UI check imports the clip twice, reviews analysis, verifies credits
and external links, exports, restarts, and checks persistence and shutdown.

### Mac packages

Use a portable or python.org Python with the required CPU architecture. A
Homebrew Python compiled for a newer macOS may produce an app that cannot run
on older Macs. The helper selects wheels for macOS 13 on Intel and macOS 14 on
Apple Silicon; the final build audits every Mach-O dependency and rejects
build-machine library paths.

```sh
.venv-build/bin/python desktop/prepare_mac_dependencies.py
.venv-build/bin/python desktop/build_mac_backend.py

# Apple Silicon
.venv-build/bin/python desktop/check_packaged_backend.py desktop/build/backend-macos-arm64/accc-backend/accc-backend
npm run pack:mac:arm64 --prefix desktop
node desktop/check_desktop.cjs "dist/mac-arm64/ACCC Telemetry.app/Contents/MacOS/ACCC Telemetry"

# Intel: run the dependency/build steps with an Intel Python first
.venv-build/bin/python desktop/check_packaged_backend.py desktop/build/backend-macos-x64/accc-backend/accc-backend
npm run pack:mac:x64 --prefix desktop
node desktop/check_desktop.cjs "dist/mac/ACCC Telemetry.app/Contents/MacOS/ACCC Telemetry"
```

An Intel Python can run under Rosetta on Apple Silicon to build and exercise the
Intel backend. The Intel and ARM output folders coexist. A Rosetta check is not
a substitute for running the app on an Intel Mac or testing the minimum OS.
Inspect each backend's `build-info.json` for its architecture and audited OS floor.

### Windows and Linux packages

Install the host's build/test dependencies once:

```sh
.venv-build/bin/python -m pip install -r requirements-desktop.txt -r requirements-dev.txt
```

Windows backend and installer:

```powershell
.venv-build\Scripts\python.exe desktop/build_windows_backend.py
.venv-build\Scripts\python.exe desktop/check_packaged_backend.py desktop/build/backend-windows/python.exe desktop/build/backend-windows/app/desktop/backend.py
npm run pack:win --prefix desktop
node desktop/check_desktop.cjs "dist/win-unpacked/ACCC Telemetry.exe"
```

Linux x64 backend and packages:

```sh
.venv-build/bin/python desktop/build_linux_backend.py --arch x64
.venv-build/bin/python desktop/check_packaged_backend.py desktop/build/backend-linux-x64/python/bin/python3 desktop/build/backend-linux-x64/app/desktop/backend.py
npm run pack:linux:x64 --prefix desktop
node desktop/check_desktop.cjs dist/linux-unpacked/accc-telemetry
```

For ARM64, use `--arch arm64`, `backend-linux-arm64`, `pack:linux:arm64`, and
`dist/linux-arm64-unpacked/accc-telemetry`. Build both backend architectures before
using `npm run pack:linux --prefix desktop` to package both in one command.

The Linux npm packaging commands run `prepare_linux_packaging.cjs` first. It
checks the pinned builder version and adjusts the generated AppImage launcher
so it does not automatically disable Chromium sandboxing. Use the npm commands
above so this preparation also runs in local and CI builds.

The Windows/Linux backend builders can assemble their runtimes on macOS. They
verify upstream archive/wheel hashes, target-specific dependency markers,
PE/ELF CPU architectures, bundled resources, and HTTPS certificates. Linux also
audits required GLIBC/GLIBCXX versions and external libraries. The native runtime
checks must run on the target OS/CPU. Package-builder system tools such as FPM
and RPM are build dependencies, not dependencies installed on app launch.

### Development launch and overrides

For `npm start --prefix desktop`, the shell uses the project's `.venv` Python by
default. Point `ACCC_PYTHON` to a different development interpreter if needed.
`ACCC_DESKTOP_USER_DATA` selects isolated desktop settings/browser storage;
`ACCC_DATA_ROOT` selects a telemetry library. New names take precedence over the
legacy `APEX_*` aliases. `ACCC_JS_RUNTIME` is set by the shell for bundled online
video helpers and also accepts the legacy `APEX_JS_RUNTIME` override.

`ACCC_SMOKE_VIDEO` supplies a test clip to the packaged desktop check;
`ACCC_SMOKE_OUTPUT` changes its report/screenshot folder. Normal users do not
need any of these variables.

## Verification and release status

See the [release notes](https://github.com/SaberDinp/ACCCT/releases/latest) and its
linked Actions run for the actual verification status of the downloaded version.
A successful package build alone does not certify runtime behavior on every OS,
distribution, or GPU. Intel testing under Rosetta is useful but does not replace
native Intel or minimum-OS testing.

The reusable `.github/workflows/desktop.yml` runs on native Apple Silicon, Intel
Mac, Windows x64, Ubuntu x64, and Ubuntu ARM64 hosts. Each job builds its backend,
checks its packaged runtime, and checks the desktop UI using a generated fixture
before uploading installers. Linux checks run under Xvfb with sandboxing enabled.

`.github/workflows/release.yml` calls those jobs for version tags. Only after all
five pass does it assemble all 14 installer/archive files, generate checksums,
and publish the GitHub Release. A failed platform job prevents publication. Main
branch pushes run source tests without duplicating all five packaging jobs.

Local contract checks are:

```sh
.venv-build/bin/python -m pytest -q tests/test_desktop_runtime.py tests/test_windows_bundle.py tests/test_linux_bundle.py
npm test --prefix desktop
```

The desktop check uses isolated temporary settings and libraries. Native
minimum-OS tests, hardware/GPU coverage, publisher signing, and notarization
remain separate release work. See the [contributor guide](../docs/DEVELOPMENT.md)
for source checks and release maintenance.
