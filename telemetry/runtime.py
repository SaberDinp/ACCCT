"""Commands shared by source checkouts and bundled desktop backends."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys


CLI_MODULES = {"yt_dlp", "streamlink", "streamlink_cli"}


def module_command(module: str) -> list[str]:
    """Re-enter the bundle's small CLI dispatcher when Python is frozen."""
    if module not in CLI_MODULES:
        raise ValueError(f"Unsupported bundled command: {module}")
    module = "streamlink" if module == "streamlink_cli" else module
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-module", module]
    return [sys.executable, "-m", module]


def javascript_runtime_args() -> list[str]:
    """Prefer the desktop's bundled JS runtime over a machine-wide install.

    ACCC_JS_RUNTIME uses yt-dlp's own syntax, for example node:/path/to/node.
    When this is Electron's executable, the desktop launcher also sets
    ELECTRON_RUN_AS_NODE=1 in its backend child's environment.
    """
    # Retain the old environment override for existing launch scripts.
    bundled = os.environ.get("ACCC_JS_RUNTIME") or os.environ.get("APEX_JS_RUNTIME")
    if bundled:
        if not bundled.startswith(("node:", "deno:")):
            raise ValueError("ACCC_JS_RUNTIME must start with node: or deno:")
        # yt-dlp adds runtimes to its defaults, rather than replacing them.
        # Clear those defaults so a machine-wide Deno cannot take precedence
        # over the known-compatible runtime supplied by the desktop shell.
        return ["--no-js-runtimes", "--js-runtimes", bundled]
    for runtime in ("deno", "node"):
        if shutil.which(runtime):
            return ["--no-js-runtimes", "--js-runtimes", runtime]
    return []


def subprocess_options() -> dict:
    """Keep download/capture helper consoles out of the Windows desktop UI."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
