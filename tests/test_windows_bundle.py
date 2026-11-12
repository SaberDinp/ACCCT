"""Cross-platform packaging checks that catch missing Windows dependencies."""
import struct
import zipfile

import pytest

from desktop.build_windows_backend import ROOT, copy_application, validate_dependencies, validate_pe
from telemetry.validation import extractor_fingerprint


def metadata_wheel(directory, name, version="1.0", requirements=()):
    path = directory / f"{name}-{version}-py3-none-any.whl"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    metadata += "".join(f"Requires-Dist: {requirement}\n" for requirement in requirements)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA", metadata)
    return path


def test_cross_build_requires_windows_only_dependencies(tmp_path):
    package = metadata_wheel(tmp_path, "example", requirements=["cffi>=2; sys_platform == 'win32'"])
    with pytest.raises(RuntimeError, match="cffi"):
        validate_dependencies([package], "3.14.10")
    cffi = metadata_wheel(tmp_path, "cffi", "2.1")
    assert validate_dependencies([package, cffi], "3.14.10") == {"cffi": "2.1", "example": "1.0"}


def test_cross_build_validates_transitive_extras_and_versions(tmp_path):
    downloader = metadata_wheel(tmp_path, "yt-dlp", requirements=["helper[decode]; extra == 'default'"])
    helper = metadata_wheel(tmp_path, "helper", requirements=["codec>=2; extra == 'decode'"])
    old_codec = metadata_wheel(tmp_path, "codec", "1.0")
    with pytest.raises(RuntimeError, match="codec>=2"):
        validate_dependencies([downloader, helper, old_codec], "3.14.10")
    codec = metadata_wheel(tmp_path, "codec", "2.0")
    validate_dependencies([downloader, helper, codec], "3.14.10")


def test_cross_build_does_not_require_posix_dependencies(tmp_path):
    package = metadata_wheel(tmp_path, "example", requirements=["uvloop; sys_platform != 'win32'"])
    assert validate_dependencies([package], "3.14.10") == {"example": "1.0"}


def test_rejects_wrong_architecture_native_binary(tmp_path):
    binary = tmp_path / "python.exe"
    header = bytearray(64)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 60, 64)
    binary.write_bytes(header + b"PE\0\0" + struct.pack("<H", 0xAA64))
    with pytest.raises(RuntimeError, match="x64"):
        validate_pe(binary)
    binary.write_bytes(header + b"PE\0\0" + struct.pack("<H", 0x8664))
    validate_pe(binary)


def test_packaged_sources_preserve_validation_fingerprint_without_user_data(tmp_path):
    app = tmp_path / "app"
    copy_application(app)
    assert extractor_fingerprint(app) == extractor_fingerprint(ROOT)
    assert {path.name for path in app.iterdir()} == {
        "main.py", "requirements.txt", "README.md", "telemetry", "web", "assets",
        "export_templates", "desktop", "scripts",
    }
    assert {path.name for path in (app / "scripts").iterdir()} == {"validate_corpus.py"}
    assert not any(app.rglob("*.sqlite3"))
    assert not any(app.rglob("*.mp4"))
    assert not any(app.rglob("__pycache__"))
