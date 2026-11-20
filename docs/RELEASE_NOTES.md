# 1.4.2

- Fix speed extraction for the Italian HUD in the reported Oulton Park reference
  (`hl5kv1-Dhv4`). Its captions previously matched a French template at the wrong
  scale, preventing speed-unit recognition and leaving distance and delta empty.
  Italian caption calibration and an additional unit-label scale restore the
  observed speed without weakening confidence checks or guessing units.
- Add **Video options → Analyze again** to refresh saved telemetry using the
  existing video. It preserves the previous analysis, source link, recording
  context, reviewed markers, and selected lap boundaries.
- These changes share the same backend, UI, and assets in the source app and all
  macOS, Windows, and Linux builds. Previously saved telemetry needs reanalysis.

Validation includes the complete 487-second 1080p reference import and API
comparison of the selected lap: speed coverage recovered from 0% to 100%, with
distance and delta available. Held-out image tests cover other timestamps,
setup screens, HUD movement, and unreadable units. Very small or heavily
compressed HUDs can still leave readings missing.

Close the older running app, install the matching **1.4.2** package, and reopen
**ACCC Telemetry**. For the affected saved reference, choose
**Reference → Video options → Analyze again**. Updating the app preserves your
library; reanalysis refreshes the old telemetry using the existing video and
keeps your selected lap boundaries. No new download is needed.

Choose an installer for your OS and CPU from the assets below. You do not need
Python or Node.js. GitHub's source-code archives are for developers.
`INSTALL.txt` has setup instructions; `SHA256SUMS.txt` verifies the app downloads.

Mac requirements: macOS 14+ on Apple Silicon; macOS 13+ on Intel. Windows requires
64-bit Windows 10/11. Linux requires a glibc desktop with GTK 3; use DEB for
Ubuntu/Debian/Mint or RPM for Fedora/openSUSE. Mac builds use ad-hoc signing for
bundle integrity, without Apple Developer ID signing or notarization. Windows
builds are unsigned. First launch may still be blocked or show an OS publisher
warning. Native build checks cover packaged backends, app launch, import/export,
restart persistence, and cached-interface upgrades on the five build targets;
they do not certify every supported OS version, distribution, or GPU.

**ACCC Telemetry is completely free for everyone to use.** Every feature is
available without a purchase, subscription, or donation. Report a bug or suggest
a feature in the [Issues tab](https://github.com/SaberDinp/ACCCT/issues); donating
is never required to report an issue or receive fixes.

We are [Rice Racing](https://www.riceracing.org/), Rice University's Formula SAE
team. Want to support the students behind the app? Visit
[giving.rice.edu](https://giving.rice.edu/), click **Make a Gift**, select **Other**,
and enter **Rice Formula SAE Club**. Thank you for helping us build and learn.
