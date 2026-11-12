"""Validate cross-built Linux architecture and dependency contracts on any OS."""
import struct

import pytest

from desktop.build_linux_backend import elf_details, pip_platforms, validate_dependencies
from tests.test_windows_bundle import metadata_wheel


def test_linux_markers_use_target_not_host(tmp_path):
    package = metadata_wheel(tmp_path, "example", requirements=[
        "linux-helper>=2; sys_platform == 'linux'", "windows-helper; sys_platform == 'win32'"])
    with pytest.raises(RuntimeError, match="linux-helper"):
        validate_dependencies([package], "3.14.10", "x64")
    helper = metadata_wheel(tmp_path, "linux-helper", "2.0")
    assert validate_dependencies([package, helper], "3.14.10", "x64") == {"example": "1.0", "linux-helper": "2.0"}


def test_linux_architecture_markers_and_transitive_extra(tmp_path):
    downloader = metadata_wheel(tmp_path, "yt-dlp", requirements=["helper[decode]; extra == 'default'"])
    helper = metadata_wheel(tmp_path, "helper", requirements=["codec>=2; extra == 'decode' and platform_machine == 'aarch64'"])
    with pytest.raises(RuntimeError, match="codec"):
        validate_dependencies([downloader, helper], "3.14.10", "arm64")
    validate_dependencies([downloader, helper], "3.14.10", "x64")
    codec = metadata_wheel(tmp_path, "codec", "2.1")
    validate_dependencies([downloader, helper, codec], "3.14.10", "arm64")


def test_linux_rejects_foreign_native_architecture(tmp_path):
    binary = tmp_path / "extension.so"
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", header, 18, 183)
    binary.write_bytes(header)
    with pytest.raises(RuntimeError, match="Linux x64"):
        elf_details(binary, "x64")
    with pytest.raises(RuntimeError, match="section headers"):
        elf_details(binary, "arm64")


def test_glibc_audit_reads_required_version_not_incidental_strings(tmp_path):
    binary = tmp_path / "extension.so"
    data = bytearray(512)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", data, 18, 62)
    struct.pack_into("<Q", data, 40, 64)
    struct.pack_into("<HH", data, 58, 64, 3)
    # Empty section, string table, and GNU version requirement table.
    strings = b"\0GLIBC_2.28\0GLIBC_9.99\0"
    data[256:256 + len(strings)] = strings
    struct.pack_into("<IIQQQQIIQQ", data, 128, 0, 3, 0, 0, 256, len(strings), 0, 0, 1, 0)
    struct.pack_into("<IIQQQQIIQQ", data, 192, 0, 0x6FFFFFFE, 0, 0, 320, 32, 1, 0, 1, 0)
    struct.pack_into("<HHIII", data, 320, 1, 1, 0, 16, 0)
    struct.pack_into("<IHHII", data, 336, 0, 0, 0, 1, 0)
    binary.write_bytes(data)
    assert elf_details(binary, "x64")["minimum_glibc"] == (2, 28)


def test_linux_wheel_targets_do_not_claim_a_newer_glibc():
    for architecture, cpu in (("x64", "x86_64"), ("arm64", "aarch64")):
        targets = pip_platforms(architecture)
        assert f"manylinux_2_28_{cpu}" in targets
        assert f"manylinux2014_{cpu}" in targets
        assert not any("manylinux_2_29_" in target for target in targets)
