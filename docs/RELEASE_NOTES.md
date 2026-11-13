# 1.4.1

This patch improves recovery when YouTube refuses a public reference video's
direct download with **HTTP 403**, and explains download failures more clearly.

- A refused direct video download retries once using an available public HLS
  stream. The app prepares it for playback while keeping the 1080p, two-hour,
  4 GB, and 20-minute import limits.
- Download errors now distinguish refused requests, account or bot verification,
  unavailable videos, connection problems, and download-component failures.
  The message includes the location of the local diagnostic log. Failed optional
  audio downloads also retain their diagnostic log.
- Installed apps consistently use their bundled JavaScript runtime for YouTube
  downloads, including when another runtime is installed on the computer.

The app downloads anonymously and does not use your browser's signed-in session.
Public videos often work without sign-in, but YouTube can still refuse a request
or offer no usable public stream. Account-restricted recordings require access
that this anonymous downloader cannot provide. If a download still fails, choose
another public reference or import a local copy you have permission to use.

[Reference library coverage, creators, and verification notes](https://github.com/SaberDinp/ACCCT/blob/v1.4.1/assets/references/README.md)

Close the older running app, install the matching **1.4.1** package, and reopen
**ACCC Telemetry**. Your saved recordings and preferences are preserved.

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
