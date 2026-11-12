#!/usr/bin/env python3
"""Build a self-contained backend for the current Mac architecture.

Run with a Python environment containing requirements-desktop.txt. The bundle
uses that interpreter's architecture. An Intel Python can run under Rosetta on
Apple Silicon to produce an x64 release; both output folders can coexist.
"""
from __future__ import annotations

import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURES = {"arm64": "arm64", "x86_64": "x64"}
EXECUTABLE = "accc-backend"


def minimum_macos(folder):
    """Read the deployment target of every Mach-O, including Python itself."""
    from macholib.MachO import MachO
    from macholib.mach_o import (LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX,
                                LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB)

    minimum = 0
    for path in folder.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as handle:
            magic = handle.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
            continue
        for header in MachO(str(path)).headers:
            for load, command, data in header.commands:
                if load.cmd == LC_BUILD_VERSION:
                    minimum = max(minimum, command.minos)
                elif load.cmd == LC_VERSION_MIN_MACOSX:
                    minimum = max(minimum, command.version)
                elif load.cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB):
                    dependency = data.split(b"\0")[0].decode()
                    if dependency.startswith("/") and not dependency.startswith(("/usr/lib/", "/System/Library/")):
                        raise RuntimeError(f"Bundle still depends on a build-machine library: {path}: {dependency}")
    return f"{minimum >> 16}.{(minimum >> 8) & 255}"


def main():
    if sys.platform != "darwin":
        raise SystemExit("The macOS backend must be built on macOS.")
    architecture = ARCHITECTURES.get(platform.machine())
    if architecture is None:
        raise SystemExit(f"Unsupported Mac architecture: {platform.machine()}")
    output = ROOT / "desktop" / "build" / f"backend-macos-{architecture}"
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(output),
        "--workpath", str(ROOT / "desktop" / "build" / f"pyinstaller-macos-{architecture}"),
        str(ROOT / "desktop" / "backend.spec"),
    ], cwd=ROOT, check=True)
    metadata = {"platform": "darwin", "architecture": architecture,
                "python": platform.python_version(), "entry": EXECUTABLE,
                "minimum_macos": minimum_macos(output / EXECUTABLE)}
    (output / EXECUTABLE / "build-info.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, output / EXECUTABLE / "LICENSE-Python.txt")
    print(f"Built {output / EXECUTABLE / EXECUTABLE}")


if __name__ == "__main__":
    main()
