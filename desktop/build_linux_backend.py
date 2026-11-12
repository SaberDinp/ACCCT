#!/usr/bin/env python3
"""Build Linux x64/arm64 app-local Python runtimes from verified archives/wheels.

Works on macOS as well as Linux; cross-build validation never pretends to execute
Linux code. Run check_packaged_backend.py on Linux before declaring it tested.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name

from desktop.build_windows_backend import checked_download, copy_application, digest, wheel_metadata
from telemetry.validation import extractor_fingerprint

BUILD = ROOT / "desktop/build"
CPUS = {"x64": "x86_64", "arm64": "aarch64"}
ELF_MACHINES = {"x64": 62, "arm64": 183}


def pip_platforms(architecture):
    cpu = CPUS[architecture]
    tags = [f"manylinux_2_{minor}_{cpu}" for minor in range(28, 16, -1)]
    tags.append(f"manylinux2014_{cpu}")
    return [value for tag in tags for value in ("--platform", tag)]


def validate_dependencies(wheels, python_version, architecture):
    """Evaluate all markers/extras for the target rather than the macOS host."""
    metadata = {canonicalize_name(item["Name"]): item for item in map(wheel_metadata, wheels)}
    environment = default_environment()
    environment.update(
        implementation_name="cpython", implementation_version=python_version,
        os_name="posix", platform_machine=CPUS[architecture],
        platform_python_implementation="CPython", platform_release="6.8.0",
        platform_system="Linux", platform_version="6.8.0",
        python_full_version=python_version, python_version=".".join(python_version.split(".")[:2]),
        sys_platform="linux", extra="",
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
                raise RuntimeError(f"Unsatisfied Linux dependency: {name} requires {requirement}")
            newly_required = requirement.extras - extras[target]
            if newly_required:
                extras[target].update(newly_required)
                pending.append(target)
    return {name: item["Version"] for name, item in sorted(metadata.items())}


def elf_details(path, architecture):
    """Read ELF64 target, loader dependencies and *required* GLIBC versions."""
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        return None
    if data[4:6] != b"\x02\x01" or struct.unpack_from("<H", data, 18)[0] != ELF_MACHINES[architecture]:
        raise RuntimeError(f"Not a Linux {architecture} ELF64 binary: {path}")
    offset = struct.unpack_from("<Q", data, 40)[0]
    size, count = struct.unpack_from("<HH", data, 58)
    if not offset or size < 64 or offset + size * count > len(data):
        raise RuntimeError(f"Missing or invalid ELF section headers: {path}")
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, offset + index * size) for index in range(count)]
    versions, cpp_versions, needed, paths = set(), set(), set(), set()

    def string(section, start):
        begin = section[4] + start
        end = data.find(b"\0", begin, section[4] + section[5])
        if end < begin:
            raise RuntimeError(f"Malformed ELF string table: {path}")
        return data[begin:end].decode()

    for section in sections:
        if section[1] == 0x6FFFFFFE:  # SHT_GNU_verneed
            strings = sections[section[6]]
            cursor, end = section[4], section[4] + section[5]
            while cursor < end:
                _version, count, _file, auxiliary, following = struct.unpack_from("<HHIII", data, cursor)
                item = cursor + auxiliary
                for _ in range(count):
                    _hash, _flags, _other, name, next_item = struct.unpack_from("<IHHII", data, item)
                    value = string(strings, name)
                    if re.fullmatch(r"GLIBC_\d+\.\d+(?:\.\d+)?", value):
                        versions.add(tuple(map(int, value.removeprefix("GLIBC_").split("."))))
                    elif re.fullmatch(r"GLIBCXX_\d+\.\d+(?:\.\d+)?", value):
                        cpp_versions.add(tuple(map(int, value.removeprefix("GLIBCXX_").split("."))))
                    if not next_item:
                        break
                    item += next_item
                if not following:
                    break
                cursor += following
        elif section[1] == 6:  # SHT_DYNAMIC
            strings = sections[section[6]]
            for cursor in range(section[4], section[4] + section[5], 16):
                tag, value = struct.unpack_from("<qQ", data, cursor)
                if tag == 1:  # DT_NEEDED
                    needed.add(string(strings, value))
                elif tag in (15, 29):
                    paths.update(string(strings, value).split(":"))
    for value in paths:
        if value.startswith("/"):
            raise RuntimeError(f"Build-machine library path in {path}: {value}")
    return {"minimum_glibc": max(versions, default=(2, 17)),
            "minimum_glibcxx": max(cpp_versions, default=(3, 4)), "needed": needed}


def validate_runtime(folder, architecture, minimum_glibc):
    native_count, actual, cpp, required = 0, (2, 17), (3, 4), set()
    names = {path.name for path in folder.rglob("*") if path.is_file()}
    for path in folder.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as handle:
            if handle.read(4) != b"\x7fELF":
                continue
        details = elf_details(path, architecture)
        native_count += 1
        actual = max(actual, details["minimum_glibc"])
        cpp = max(cpp, details["minimum_glibcxx"])
        for dependency in details["needed"]:
            if "$ORIGIN" in dependency:
                resolved = Path(dependency.replace("$ORIGIN", str(path.parent))).resolve()
                if not resolved.is_relative_to(folder.resolve()) or not resolved.is_file():
                    raise RuntimeError(f"Unresolved bundled library in {path}: {dependency}")
                required.add(resolved.name)
            elif "/" in dependency:
                raise RuntimeError(f"Nonportable library dependency in {path}: {dependency}")
            else:
                required.add(dependency)
    if actual > tuple(map(int, minimum_glibc.split("."))):
        raise RuntimeError(f"Runtime requires GLIBC {actual}, above declared {minimum_glibc}")
    if native_count < 10:
        raise RuntimeError("The Linux runtime is missing native libraries")
    return {"native_files_checked": native_count, "minimum_glibc": ".".join(map(str, actual)),
            "minimum_glibcxx": ".".join(map(str, cpp)),
            "external_libraries": sorted(required - names)}


def build(architecture):
    lock = json.loads((ROOT / f"desktop/requirements-linux-{architecture}.lock.json").read_text(encoding="utf-8"))
    if lock["architecture"] != architecture:
        raise RuntimeError("Linux lock architecture mismatch")
    runtime = lock["python"]
    BUILD.mkdir(exist_ok=True)
    archive = BUILD / runtime["filename"]
    checked_download(runtime["url"], archive, runtime["sha256"])
    wheelhouse = BUILD / f"linux-wheelhouse-{architecture}"
    wheelhouse.mkdir(exist_ok=True)
    requirements = BUILD / f"linux-{architecture}-resolved-requirements.txt"
    requirements.write_text("\n".join(
        f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}" for item in lock["packages"]
    ) + "\n", encoding="utf-8")
    selection = ["--only-binary=:all:", *pip_platforms(architecture), "--python-version", "3.14",
                 "--implementation", "cp", "--abi", "cp314"]
    subprocess.run([sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
                    "--dest", str(wheelhouse), *selection, "--no-deps", "--require-hashes",
                    "-r", str(requirements)], check=True)
    wheels = []
    for item in lock["packages"]:
        wheel = wheelhouse / item["filename"]
        if not wheel.is_file() or digest(wheel) != item["sha256"]:
            raise RuntimeError(f"Missing or altered Linux wheel: {wheel.name}")
        wheels.append(wheel)
    packages = validate_dependencies(wheels, runtime["version"], architecture)
    output = BUILD / f"backend-linux-{architecture}"
    with tempfile.TemporaryDirectory(prefix=f"linux-{architecture}-", dir=BUILD) as temporary:
        staging = Path(temporary) / "backend"
        staging.mkdir()
        with tarfile.open(archive) as source:
            # ncurses ships case-sensitive aliases that collide on the default
            # macOS filesystem. The headless backend never uses a terminal UI;
            # omit its unused terminal database instead of corrupting symlinks.
            members = (member for member in source if not member.name.startswith("python/share/terminfo/"))
            source.extractall(staging, members=members, filter="data")
        # Electron provides the GUI. Tk is unused, adds an X11 dependency, and
        # upstream's Tcl carries build-machine RPATHs; omit the whole Tk stack.
        unused = ["python/lib/libtcl*", "python/lib/libtk*", "python/lib/tcl*", "python/lib/tk*",
                  "python/lib/python3.14/tkinter", "python/lib/python3.14/idlelib",
                  "python/lib/python3.14/turtledemo", "python/lib/python3.14/turtle.py",
                  "python/lib/python3.14/lib-dynload/_tkinter*", "python/bin/idle*",
                  "python/bin/tclsh*", "python/bin/wish*"]
        for pattern in unused:
            for item in staging.glob(pattern):
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        library = staging / "python/lib/python3.14/site-packages"
        # Standalone Python includes pip; applications do not need installers.
        shutil.rmtree(library)
        library.mkdir()
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                        "--target", str(library), "--no-deps", "--no-compile", "--no-index",
                        *selection, *(str(wheel) for wheel in wheels)], check=True)
        shutil.rmtree(library / "bin", ignore_errors=True)
        for executable in (staging / "python/bin").glob("pip*"):
            executable.unlink()
        copy_application(staging / "app")
        assert extractor_fingerprint(staging / "app") == extractor_fingerprint(ROOT)
        entry = staging / "python/bin/python3"
        if not entry.is_file():
            raise RuntimeError("Python entrypoint is missing")
        entry.chmod(entry.stat().st_mode | 0o755)
        certificate = library / "certifi/cacert.pem"
        if not certificate.is_file() or certificate.stat().st_size < 10000:
            raise RuntimeError("Missing HTTPS certificate store")
        report = {"format": "accc-linux-backend-v1", "platform": "linux", "architecture": architecture,
                  "python": runtime, "packages": packages, "runtime_tested": False,
                  **validate_runtime(staging, architecture, lock["minimum_glibc"])}
        if sys.platform == "linux" and platform.machine() == CPUS[architecture]:
            environment = dict(os.environ)
            environment.pop("PYTHONHOME", None)
            environment.pop("PYTHONPATH", None)
            subprocess.run([str(entry), "-c", "import av, cv2, fastapi, numpy, streamlink, yt_dlp; "
                            "print('Linux backend imports passed')"], cwd=staging, env=environment, check=True)
            report["runtime_tested"] = True
        (staging / "bundle-manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            if not (output / "bundle-manifest.json").is_file():
                raise RuntimeError(f"Refusing to replace an unrecognized directory: {output}")
            shutil.rmtree(output)
        shutil.move(str(staging), output)
    print(f"Linux {architecture} backend ready: {output}")
    print(json.dumps({key: report[key] for key in ("native_files_checked", "minimum_glibc", "external_libraries", "runtime_tested")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=tuple(CPUS), required=True)
    build(parser.parse_args().arch)
