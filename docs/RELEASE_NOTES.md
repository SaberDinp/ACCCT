# 1.3.0

A simpler recorded-lap workflow, shared by every macOS, Windows, and Linux app.

- **Analysis** and **Credits** are the only pages. Choose or import recordings
  through **Video options**; the extra Recordings, Calibration, and top import
  buttons have been removed. HUD detection runs automatically.
- Select **Start of lap** or **End of lap**, inspect the frame, and press **Lock**.
  The picker title names the boundary you are selecting. Segment-number controls,
  separate save/search actions, recording-label editing, car labels, and manual
  distance-registration controls are removed.
- Track guide progress is automatic. Circuit detection uses the available lap
  telemetry and leaves uncertain matches for you to choose manually. Your manual
  circuit choice takes priority.
- Existing recordings and preferences are preserved. The cached-interface upgrade
  fix from 1.2.4 is included.

Close the older running app, install the matching **1.3.0** package, and reopen
**ACCC Telemetry**.

Choose an installer for your OS and CPU from the assets below. You do not need
Python or Node.js. GitHub's source-code archives are for developers.
`INSTALL.txt` has setup instructions; `SHA256SUMS.txt` verifies the app downloads.

Mac requirements: macOS 14+ on Apple Silicon; macOS 13+ on Intel. Windows requires
64-bit Windows 10/11. Linux requires a glibc desktop with GTK 3; use DEB for
Ubuntu/Debian/Mint or RPM for Fedora/openSUSE. Mac builds use ad-hoc signing for
bundle integrity, without Apple Developer ID signing or notarization. Windows
builds are unsigned. First launch may still be blocked or show an OS publisher
warning.

**ACCC Telemetry is completely free for everyone to use.** Every feature is
available without a purchase, subscription, or donation. Report a bug or suggest
a feature in the [Issues tab](https://github.com/SaberDinp/ACCCT/issues); donating
is never required to report an issue or receive fixes.

We are [Rice Racing](https://www.riceracing.org/), Rice University's Formula SAE
team. Want to support the students behind the app? Visit
[giving.rice.edu](https://giving.rice.edu/), click **Make a Gift**, select **Other**,
and enter **Rice Formula SAE Club**. Thank you for helping us build and learn.
