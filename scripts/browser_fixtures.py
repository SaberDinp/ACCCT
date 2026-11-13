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


async def lock_lap_boundary(page, side, edge, value):
    """Pick one boundary through the same modal workflow used by desktop apps."""
    from playwright.async_api import expect
    await page.locator(f"#{side}{edge}Boundary").click()
    await expect(page.locator("#alignmentTitle")).to_have_text(f"Selecting {edge.lower()} of lap")
    await page.locator("#alignmentTime").fill(str(value))
    await expect(page.locator("#alignmentLock")).to_be_enabled()
    await page.locator("#alignmentLock").click()
    await expect(page.locator("#alignmentDialog")).to_be_hidden()
