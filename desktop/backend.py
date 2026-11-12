"""Private backend process for the desktop shell; no system Python is required.

The shell owns stdin. A ``shutdown`` line or EOF requests a graceful shutdown.
Only the readiness record is written to stdout; server logs go to stderr.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import threading


if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def dispatch_module(arguments: list[str]) -> bool:
    if not arguments or arguments[0] != "--run-module":
        return False
    from telemetry.runtime import CLI_MODULES

    if len(arguments) < 2 or arguments[1] not in CLI_MODULES:
        raise ValueError("Only yt_dlp and streamlink are supported helper commands.")
    module = "streamlink" if arguments[1] == "streamlink_cli" else arguments[1]
    sys.argv = [module, *arguments[2:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return True


def watch_parent(server) -> None:
    """EOF covers an Electron crash as well as an ordinary window close."""
    try:
        if sys.stdin is not None:
            for line in sys.stdin:
                if line.strip() == "shutdown":
                    break
    finally:
        server.should_exit = True


def serve(data_root: Path, host: str, port: int) -> None:
    import uvicorn
    from main import create_app

    data_root = data_root.expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if sys.platform == "win32":
            # Windows SO_REUSEADDR can steal an active listener. Exclusive
            # ownership protects the origin the desktop window trusts.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            # Reuse our saved port after HTTP connections leave TCP
            # TIME_WAIT entries, preserving the UI's localStorage origin.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Bind once and pass the socket through; picking then releasing a free
        # port would let another process steal it before uvicorn starts.
        try:
            listener.bind((host, port))
        except OSError as error:
            if not port or (error.errno not in {errno.EADDRINUSE, 10048}
                            and getattr(error, "winerror", None) != 10048):
                raise
            # A saved port preserves the browser's local settings between runs.
            # If another app has claimed it, start our own server on a fresh
            # port; never trust or reuse an unknown listener already there.
            listener.bind((host, 0))
        actual_port = listener.getsockname()[1]
        app = create_app(root=data_root, port=actual_port)

        class DesktopServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                if self.started and not self.should_exit:
                    print(json.dumps({"type": "ready", "host": host,
                                      "port": actual_port, "pid": os.getpid()}), flush=True)

        config = uvicorn.Config(app, host=host, port=actual_port, log_level="info",
                                access_log=False, loop="asyncio", http="h11",
                                ws="websockets", timeout_graceful_shutdown=20)
        server = DesktopServer(config)
        threading.Thread(target=watch_parent, args=(server,), daemon=True,
                         name="desktop-parent-watch").start()
        server.run(sockets=[listener])


def main(arguments: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if arguments is None else arguments
    if dispatch_module(arguments):
        return
    parser = argparse.ArgumentParser(description="ACCC Telemetry desktop backend")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1",))
    parser.add_argument("--port", default=0, type=int)
    args = parser.parse_args(arguments)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    serve(args.data_root, args.host, args.port)


if __name__ == "__main__":
    # PyInstaller's Windows bootloader may re-enter for multiprocessing helpers.
    import multiprocessing

    multiprocessing.freeze_support()
    main()
