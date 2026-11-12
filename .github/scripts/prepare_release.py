"""Require the complete native build set before preparing a public release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SUFFIXES = (
    "mac-arm64.dmg", "mac-arm64.zip", "mac-x64.dmg", "mac-x64.zip",
    "win-x64.exe", "win-x64.zip", "linux-x86_64.AppImage", "linux-arm64.AppImage",
    "linux-amd64.deb", "linux-arm64.deb", "linux-x86_64.rpm", "linux-aarch64.rpm",
    "linux-x64.tar.gz", "linux-arm64.tar.gz",
)


def prepare(folder: Path) -> None:
    version = json.loads((ROOT / "desktop/package.json").read_text())["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit("Expected a stable x.y.z version.")
    release_tag = os.environ.get("RELEASE_TAG", f"v{version}")
    if release_tag != f"v{version}":
        raise SystemExit("Release tag does not match desktop/package.json.")
    expected = {f"ACCC-Telemetry-{version}-{suffix}" for suffix in SUFFIXES}
    actual = {p.name for p in folder.iterdir() if p.is_file()}
    # A retry may see previously generated metadata, but never extra app files.
    actual -= {"SHA256SUMS.txt", "INSTALL.txt"}
    if actual != expected:
        raise SystemExit(f"Incomplete or unexpected release files. Missing: {sorted(expected-actual)}; unexpected: {sorted(actual-expected)}")
    lines = []
    for name in sorted(expected):
        path = folder / name
        before = path.stat()
        if before.st_size < 1_000_000:
            raise SystemExit(f"Installer is unexpectedly small: {name}")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SystemExit(f"Installer changed while hashing: {name}")
        lines.append(f"{digest}  {name}\n")
    (folder / "SHA256SUMS.txt").write_text("".join(lines), encoding="ascii")
    install = (ROOT / "docs/INSTALL.txt").read_text().replace("{version}", version)
    (folder / "INSTALL.txt").write_text(install)
    notes = (ROOT / "docs/RELEASE_NOTES.md").read_text()
    if f"# {version}" not in notes:
        raise SystemExit("Update docs/RELEASE_NOTES.md for this app version before publishing.")
    run_url = "https://github.com/{}/actions/runs/{}".format(
        os.environ.get("GITHUB_REPOSITORY", "SaberDinp/ACCCT"),
        os.environ.get("GITHUB_RUN_ID", ""),
    )
    if os.environ.get("GITHUB_RUN_ID"):
        notes += f"\nNative build and desktop smoke checks passed for all five targets in [this Actions run]({run_url}).\n"
    (ROOT / "release-notes.md").write_text(notes)
    print(f"Prepared {len(expected)} installers/archives, SHA256SUMS.txt, and INSTALL.txt for {release_tag}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    prepare(parser.parse_args().artifact_dir)
