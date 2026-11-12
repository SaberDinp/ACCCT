#!/usr/bin/env python3
"""Install Mac build dependencies using wheels compatible with the release OS.

Run inside a virtual environment. Intel targets macOS 13; Apple Silicon targets
macOS 14. The backend builder independently audits actual Mach-O deployment
targets, since wheel filenames alone cannot prove runtime compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"x86_64": ("x64", "macosx_13_0_x86_64", "13.0"),
           "arm64": ("arm64", "macosx_14_0_arm64", "14.0")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--without-dev", action="store_true", help="Omit pytest, HTTP test client and browser test dependencies.")
    arguments = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() not in TARGETS:
        parser.error("Run this with an Intel or Apple Silicon Python on macOS.")
    if sys.prefix == sys.base_prefix:
        parser.error("Activate a build virtual environment before installing dependencies.")
    architecture, target, minimum = TARGETS[platform.machine()]
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
    requirements = ["-r", str(ROOT / "requirements-desktop.txt")]
    if not arguments.without_dev:
        requirements += ["-r", str(ROOT / "requirements-dev.txt")]
    build = ROOT / "desktop" / "build"
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"macos-{architecture}-wheels-", dir=build) as temporary:
        folder = Path(temporary)
        subprocess.run([
            sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
            "--only-binary=:all:", "--platform", target, "--python-version", python_version,
            "--implementation", "cp", "--abi", abi, "--dest", str(folder), *requirements,
        ], check=True)
        wheels = sorted(folder.glob("*.whl"))
        if not wheels:
            raise RuntimeError("No compatible dependency wheels were downloaded.")
        inventory = []
        for wheel in wheels:
            with wheel.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            with zipfile.ZipFile(wheel) as archive:
                if not any(name.endswith(".dist-info/METADATA") for name in archive.namelist()):
                    raise RuntimeError(f"Invalid wheel: {wheel.name}")
            inventory.append({"filename": wheel.name, "sha256": digest})
        # Install only the exact freshly resolved wheel set, so stale wheels
        # from a newer macOS build cannot be selected by the host's tags.
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--no-index", "--no-deps", *(str(wheel) for wheel in wheels),
        ], check=True)
    report = {"architecture": architecture, "python": python_version,
              "wheel_platform": target, "minimum_macos": minimum,
              "includes_development_dependencies": not arguments.without_dev,
              "wheels": inventory}
    output = build / f"macos-{architecture}-dependency-inventory.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Installed {len(inventory)} wheels for {architecture}, macOS {minimum}+; inventory: {output}")


if __name__ == "__main__":
    main()
