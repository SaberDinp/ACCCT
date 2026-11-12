# -*- mode: python ; coding: utf-8 -*-
"""Bundle the local backend, including every immutable UI/analysis resource."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / name), name) for name in ("web", "assets", "export_templates")]
# Compatibility coverage computes a hash of the extractor's actual source.
datas += [(str(source), "telemetry") for source in (ROOT / "telemetry").glob("*.py")]
datas += [(str(ROOT / "scripts" / "validate_corpus.py"), "scripts")]
hiddenimports = ["main", "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
                 "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets.websockets_impl",
                 "uvicorn.lifespan.on"]
binaries = []
for package in ("av", "uvicorn", "yt_dlp", "yt_dlp_ejs", "streamlink", "streamlink_cli"):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
for distribution in ("streamlink", "yt-dlp", "yt-dlp-ejs"):
    datas += copy_metadata(distribution, recursive=True)

analysis = Analysis(
    [str(ROOT / "desktop" / "backend.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "playwright", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz, analysis.scripts, [],
    exclude_binaries=True,
    name="accc-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
collection = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False,
    upx=False,
    name="accc-backend",
)
