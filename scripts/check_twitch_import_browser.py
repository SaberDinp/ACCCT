"""Isolated URL-import routing and concise map-tooltip browser checks."""
import asyncio
import functools
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from check_analysis_layout_browser import AnalysisFixtures, ROOT, SOCKET_FIXTURE, StaticHandler, CURRENT
from telemetry.tracks import get_track


class ImportFixtures(AnalysisFixtures):
    def __init__(self, clip):
        super().__init__(clip)
        self.imports = []

    async def route(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == '/api/imports' and request.method == 'POST':
            self.imports.append(request.post_data_json)
            return await route.fulfill(json={'id': 'isolated-import', 'state': 'queued'})
        if path == '/api/tracks/silverstone':
            return await route.fulfill(json=get_track('silverstone'))
        return await super().route(route)


async def main():
    output = ROOT / 'artifacts/twitch-ui-review'
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(StaticHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            fixture = ImportFixtures(ROOT / 'artifacts/desktop-video/source-1080p.mp4')
            await page.route('**/api/**', fixture.route)
            await page.add_init_script(SOCKET_FIXTURE)
            preferences = {'axis': 'distance', 'current': {'id': CURRENT, 'range': 'custom', 'start': 0, 'end': 7.8, 'segment': '1'}, 'reference': {'id': ''}}
            await page.add_init_script('localStorage.setItem("accc.comparison.v1",' + json.dumps(json.dumps(preferences)) + ');')
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(f'http://127.0.0.1:{server.server_port}')
            await page.get_by_role('button', name='Analysis', exact=True).click()
            await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=20000)
            await expect(page.locator('#trackSelect')).to_have_value('silverstone')
            await page.wait_for_function('document.querySelectorAll(".track-corner").length===18')
            corner = page.locator('.track-corner').first
            await corner.hover()
            await expect(corner.locator('.track-annotation-tip')).to_have_text('Turn 1 · Copse')
            tips = await page.locator('.track-annotation-tip').all_text_contents()
            assert not any('Approximate diagram position' in tip for tip in tips)
            links = [('https://www.twitch.tv/videos/1234567890', 'twitch'),
                     ('https://clips.twitch.tv/ExampleClipSlug', 'twitch'),
                     ('https://www.twitch.tv/example/clip/ExampleClipSlug', 'twitch'),
                     ('https://youtu.be/c0SJgXUahEM', 'youtube'),
                     ('https://example.com/video.mp4', 'url')]
            for url, expected_kind in links:
                await page.locator('#openReferenceImport').click()
                await page.get_by_role('button', name='Video link', exact=True).click()
                await page.locator('#referenceUrl').fill(url)
                await page.locator('#referenceName').fill('URL routing check')
                await page.locator('#startReferenceImport').click()
                await expect(page.locator('#referenceImportDialog')).not_to_be_visible()
                assert fixture.imports[-1] == {'kind': expected_kind, 'url': url,
                                               'name': 'URL routing check', 'auto_calibrate': True, 'role': 'reference'}
            await page.locator('#openReferenceImport').click()
            await page.locator('#referenceUrl').fill('file:///etc/passwd')
            await page.locator('#startReferenceImport').click()
            await expect(page.locator('#referenceImportError')).to_have_text('Use an HTTP or HTTPS video URL.')
            assert len(fixture.imports) == len(links)
            await page.locator('#closeReferenceImport').click()
            await page.locator('#openReferenceImport').click()
            await page.locator('#referenceUrl').fill('https://www.twitch.tv/videos/1234567890')
            await page.screenshot(path=str(output / 'twitch-import.png'))
            assert not errors, errors
            assert not fixture.unexpected, fixture.unexpected
            await browser.close()
    finally:
        server.shutdown()
    report = {'passed': True, 'import_requests': fixture.imports, 'clean_map_tooltips': True,
              'invalid_protocol_rejected': True, 'page_errors': errors,
              'scope': 'Isolated API fixtures; no network downloads or changes to the personal library.'}
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
