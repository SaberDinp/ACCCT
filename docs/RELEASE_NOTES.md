# 1.2.4

This update fixes an upgrade issue where an older, cached Monitor interface could
still appear after installing a newer ACCC Telemetry app.

- Each launch now clears the HTTP cache before loading the app's bundled UI.
  The current **Analysis** and **Credits** layout appears consistently after an
  update on macOS, Windows, and Linux.
- Only cached web responses are cleared. Recordings, saved sessions, calibration,
  and app preferences stay intact.
- Analysis opens first, with **Recordings** and **Calibration** available inside
  it. The app remains focused on recorded-lap analysis and comparison.

Install the matching 1.2.4 package, close any older running copy, and reopen
**ACCC Telemetry**. On older versions, **View > Force Reload** can refresh a stale
interface: **Command+Shift+R** on macOS or **Ctrl+Shift+R** on Windows/Linux.
Version 1.2.4 handles this automatically on launch.

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
