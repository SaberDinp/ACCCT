# 1.2.0

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

Choose an installer for your OS and CPU from the assets below. You do not need
Python or Node.js. GitHub's source-code archives are for developers.
`INSTALL.txt` has setup instructions; `SHA256SUMS.txt` verifies the app downloads.

Mac requirements: macOS 14+ on Apple Silicon; macOS 13+ on Intel. Windows requires
64-bit Windows 10/11. Linux requires a glibc desktop with GTK 3; use DEB for
Ubuntu/Debian/Mint or RPM for Fedora/openSUSE. Native runner checks cover the
packaged backend and unpacked app UI, not every installer interaction, supported
OS version, Linux distribution, or GPU. These builds are unsigned and the Mac apps
are not Apple-notarized; first launch may show an OS publisher warning.

We are [Rice Racing](https://www.riceracing.org/), Rice University's Formula SAE
team. Want to support the students behind the app? Visit
[giving.rice.edu](https://giving.rice.edu/), click **Make a Gift**, select **Other**,
and enter **Rice Formula SAE Club**. Thank you for helping us build and learn.
