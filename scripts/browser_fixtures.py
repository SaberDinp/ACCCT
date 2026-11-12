"""Isolated local UI fixtures; using a live-monitor socket is a test failure."""
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class StaticHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/static/"):
            self.path = self.path.removeprefix("/static")
        super().do_GET()

    def log_message(self, *_args):
        pass

SOCKET_FIXTURE = """window.WebSocket = class {
  constructor() { throw new Error('Recorded analysis must not connect to live monitoring'); }
};"""
