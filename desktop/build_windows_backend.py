#!/usr/bin/env python3
"""Bundle Windows Python and wheels without executing Windows code on the host.

The official embeddable distribution is intended for app-local runtimes:
https://docs.python.org/3.14/using/windows.html#the-embeddable-package
The archive digest is published on the Python release page in the lock file.
Build with a host Python that has pip; users of the finished app need neither.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from urllib.request import urlopen
import zipfile

from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "desktop/requirements-windows.lock.json"
BUILD = ROOT / "desktop/build"
OUTPUT = BUILD / "backend-windows"


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def checked_download(url, destination, expected):
    if destination.exists() and digest(destination) == expected:
        return
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if digest(temporary) != expected:
            raise RuntimeError(f"SHA-256 mismatch for {destination.name}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def wheel_metadata(path):
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise RuntimeError(f"Unexpected wheel metadata in {path.name}")
        return BytesParser().parsebytes(archive.read(names[0]))


def validate_dependencies(wheels, python_version):
    """pip's cross-platform switches do not switch every environment marker.

    Check the entire selected dependency graph against Windows, including the
    yt-dlp default extra, so a successful macOS pip download cannot hide missing
    Windows-only dependencies (notably cffi and colorama).
    """
    metadata = {canonicalize_name(item["Name"]): item for item in map(wheel_metadata, wheels)}
    environment = default_environment()
    environment.update(
        implementation_name="cpython", implementation_version=python_version,
        os_name="nt", platform_machine="AMD64", platform_python_implementation="CPython",
        platform_release="10", platform_system="Windows", platform_version="10.0",
        python_full_version=python_version, python_version=".".join(python_version.split(".")[:2]),
        sys_platform="win32", extra="",
    )
    extras = {name: {""} for name in metadata}
    extras.setdefault("yt-dlp", set()).add("default")
    pending = list(metadata)
    while pending:
        name = pending.pop()
        for raw in metadata[name].get_all("Requires-Dist", []):
            requirement = Requirement(raw)
            if requirement.marker and not any(
                requirement.marker.evaluate({**environment, "extra": extra}) for extra in extras[name]
            ):
                continue
            target = canonicalize_name(requirement.name)
            installed = metadata.get(target)
            if installed is None or not requirement.specifier.contains(installed["Version"]):
                raise RuntimeError(f"Unsatisfied Windows dependency: {name} requires {requirement}")
            newly_required = requirement.extras - extras[target]
            if newly_required:
                extras[target].update(newly_required)
                pending.append(target)
    return {name: item["Version"] for name, item in sorted(metadata.items())}


def validate_pe(path):
    with path.open("rb") as handle:
        header = handle.read(64)
        if header[:2] != b"MZ":
            raise RuntimeError(f"Not a Windows executable: {path}")
        handle.seek(struct.unpack_from("<I", header, 60)[0])
        pe = handle.read(6)
        if pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != 0x8664:
            raise RuntimeError(f"Not a Windows x64 executable: {path}")


def copy_application(destination):
    destination.mkdir(parents=True)
    for filename in ("main.py", "requirements.txt", "README.md"):
        shutil.copy2(ROOT / filename, destination / filename)
    # Explicit allowlist: never ship recordings, sessions, profiles or test data.
    for directory in ("telemetry", "web", "assets", "export_templates"):
        shutil.copytree(ROOT / directory, destination / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    (destination / "desktop").mkdir()
    shutil.copy2(ROOT / "desktop/backend.py", destination / "desktop/backend.py")
    shutil.copy2(ROOT / "desktop/README.md", destination / "desktop/README.md")
    # Compatibility coverage fingerprints this source file even when no corpus
    # exists; omitting it breaks /api/validation on a fresh installation.
    (destination / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/validate_corpus.py", destination / "scripts/validate_corpus.py")


def build():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    runtime = lock["python"]
    BUILD.mkdir(exist_ok=True)
    archive = BUILD / runtime["filename"]
    checked_download(runtime["url"], archive, runtime["sha256"])
    wheelhouse = BUILD / "windows-wheelhouse-314"
    wheelhouse.mkdir(exist_ok=True)
    locked_requirements = BUILD / "windows-resolved-requirements.txt"
    locked_requirements.write_text("\n".join(
        f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}"
        for item in lock["packages"]
    ) + "\n", encoding="utf-8")
    subprocess.run([
        sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
        "--dest", str(wheelhouse), "--only-binary=:all:", "--platform", "win_amd64",
        "--python-version", "3.14", "--implementation", "cp", "--abi", "cp314",
        "--no-deps", "--require-hashes", "-r", str(locked_requirements),
    ], check=True)
    wheels = []
    for item in lock["packages"]:
        wheel = wheelhouse / item["filename"]
        if not wheel.is_file() or digest(wheel) != item["sha256"]:
            raise RuntimeError(f"Missing or altered locked wheel: {wheel.name}")
        wheels.append(wheel)
    packages = validate_dependencies(wheels, runtime["version"])

    with tempfile.TemporaryDirectory(prefix="windows-backend-", dir=BUILD) as temporary:
        staging = Path(temporary) / "backend"
        staging.mkdir()
        with zipfile.ZipFile(archive) as embedded:
            embedded.extractall(staging)
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--target", str(staging / "Lib/site-packages"), "--no-deps", "--no-compile",
            "--no-index", "--only-binary=:all:", "--platform", "win_amd64",
            "--python-version", "3.14", "--implementation", "cp", "--abi", "cp314",
            *(str(wheel) for wheel in wheels),
        ], check=True)
        # Host-generated console scripts are unnecessary: subprocesses use python -m.
        for scripts_directory in ("bin", "Scripts"):
            shutil.rmtree(staging / "Lib/site-packages" / scripts_directory, ignore_errors=True)
        (staging / "python314._pth").write_text(
            "python314.zip\n.\nLib/site-packages\napp\nimport site\n", encoding="utf-8"
        )
        copy_application(staging / "app")
        validate_pe(staging / "python.exe")
        native_files = sorted([*staging.rglob("*.pyd"), *staging.rglob("*.dll")])
        for native in native_files:
            validate_pe(native)
        certificate = staging / "Lib/site-packages/certifi/cacert.pem"
        if not certificate.is_file() or certificate.stat().st_size < 10000:
            raise RuntimeError("The bundle is missing its HTTPS certificate store")
        report = {
            "format": "accc-windows-backend-v1", "platform": "win32", "architecture": "x64",
            "python": runtime, "packages": packages, "native_files_checked": len(native_files),
            "runtime_tested": sys.platform == "win32",
        }
        if sys.platform == "win32":
            subprocess.run([str(staging / "python.exe"), "-c",
                            "import av, cv2, fastapi, numpy, streamlink, yt_dlp, main; "
                            "from telemetry.vision import HUDExtractor; "
                            "from telemetry.profiles import default_profile; "
                            "HUDExtractor(default_profile()); print('Windows backend imports passed')"],
                           cwd=staging, check=True)
        (staging / "bundle-manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if OUTPUT.exists():
            if not (OUTPUT / "bundle-manifest.json").is_file():
                raise RuntimeError(f"Refusing to replace an unrecognized directory: {OUTPUT}")
            shutil.rmtree(OUTPUT)
        shutil.move(str(staging), OUTPUT)
    print(f"Windows x64 backend ready: {OUTPUT}")
    print(f"Validated {len(packages)} locked packages and {len(native_files)} Windows x64 binaries.")
    if sys.platform != "win32":
        print("Cross-built package: run the Windows smoke check before distributing as tested.")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    build()
