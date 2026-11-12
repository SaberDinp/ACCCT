# 1.2.1

ACCC Telemetry brings recorded-lap analysis to console racers, with desktop apps
for Apple Silicon and Intel Macs, Windows x64, and Linux x64/ARM64.

- One A inside three nested Cs now identifies the app everywhere.
- Analysis opens first. Import your run and a reference recording, inspect their
  inputs, and compare laps; live monitoring, capture, and phone streaming have
  been removed from the app.
- The new Credits page introduces Rice Racing with real team photos and clear
  instructions for supporting Rice Formula SAE Club through Rice University.
- Windows setup installs for the current user in one click. Mac DMGs and Linux
  DEB/RPM packages provide the standard installation flow for each platform.
- Existing libraries are retained separately from the application.
- Windows startup recognizes the Python launcher process used by virtual
  environments. Mac app bundles use ad-hoc signing to preserve bundle integrity.

Choose an installer for your OS and CPU from the assets below. You do not need
Python or Node.js. GitHub's source-code archives are for developers.
`INSTALL.txt` has setup instructions; `SHA256SUMS.txt` verifies the app downloads.

Mac requirements: macOS 14+ on Apple Silicon; macOS 13+ on Intel. Windows requires
64-bit Windows 10/11. Linux requires a glibc desktop with GTK 3; use DEB for
Ubuntu/Debian/Mint or RPM for Fedora/openSUSE. Native runner checks exercise Mac
app bundles, silent per-user Windows installation with Desktop/Start menu
shortcuts, and DEB installation on Ubuntu 24.04 x64 and ARM64. Backend and UI
checks run against the installed Windows and Ubuntu apps. They do not cover
RPM/AppImage installation, every installer interaction, supported OS version,
Linux distribution, or GPU. Mac builds use ad-hoc signing for bundle integrity,
without Apple Developer ID signing or notarization. Windows builds are unsigned.
First launch may still be blocked or show an OS publisher warning.

We are [Rice Racing](https://www.riceracing.org/), Rice University's Formula SAE
team. Want to support the students behind the app? Visit
[giving.rice.edu](https://giving.rice.edu/), click **Make a Gift**, select **Other**,
and enter **Rice Formula SAE Club**. Thank you for helping us build and learn.
