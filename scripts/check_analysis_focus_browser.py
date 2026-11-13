"""Recorded import roles and the simplified analysis-only navigation."""
import asyncio
import functools
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from browser_fixtures import ROOT, SOCKET_FIXTURE, StaticHandler
from check_delete_swap_browser import Fixtures, CURRENT, REFERENCE


class RecordingFixtures(Fixtures):
    def __init__(self):
        super().__init__()
        self.imports, self.jobs = [], []

    async def route(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == '/api/sources':
            return await route.fulfill(json=[{'name': 'My saved lap.mp4', 'path': 'fixture.mp4'}])
        if path == '/api/imports':
            if request.method == 'GET':
                return await route.fulfill(json=self.jobs)
            body = request.post_data_json
            self.imports.append(body)
            job = {'id': f'import-{len(self.imports)}', 'state': 'completed', 'role': body['role'],
                   'session_id': CURRENT if body['role'] == 'current' else REFERENCE}
            self.jobs.insert(0, job)
            return await route.fulfill(json=job)
        return await super().route(route)


async def main():
    output = ROOT / 'artifacts/analysis-focus'
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(StaticHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors, requests, checks = [], [], []
    fixture = RecordingFixtures()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(urlparse(request.url).path))
            await page.route('**/api/**', fixture.route)
            await page.add_init_script(SOCKET_FIXTURE)
            await page.goto(f'http://127.0.0.1:{server.server_port}')
            await expect(page.locator('#view-analysis')).to_be_visible()
            assert await page.locator('.main-tabs button').all_text_contents() == ['Analysis', 'Credits']
            assert await page.locator('#view-monitor, #view-calibration, #view-sessions, #importRecording, #phoneDialog, #sourceDialog, #captureVideo').count() == 0
            checks.append('Analysis is default; primary navigation is Analysis and Credits')

            for role, expected in [('current', CURRENT), ('reference', REFERENCE)]:
                await page.locator('#currentSession' if role == 'current' else '#referenceSession').select_option('')
                await page.locator(f'#{role}ImportRecording').locator('xpath=ancestor::details').evaluate_all(
                    'elements => elements.forEach(element => {element.open = true;})')
                await page.locator(f'#{role}ImportRecording').click()
                await expect(page.locator('#import-file')).to_be_visible()
                await page.locator('#recordingImportRole').select_option(role)
                await page.locator('#referenceLocalFile').select_option('fixture.mp4')
                await page.locator('#referenceName').fill('Your saved lap' if role == 'current' else 'Reference lap')
                await page.locator('#startReferenceImport').click()
                await expect(page.locator('#referenceImportDialog')).to_be_hidden()
                await expect(page.locator('#currentSession' if role == 'current' else '#referenceSession')).to_have_value(expected)
                assert fixture.imports[-1]['role'] == role and fixture.imports[-1]['kind'] == 'file'
            checks.append('Local recorded imports select Your run or Reference according to explicit role')

            removed = ['comparisonAlignmentMode', 'confirmAlignment', 'confirmFinishAlignment',
                       'confirmSameTrack', 'trackProgressMode', 'trackManualControls',
                       'trackcurrentCar', 'trackreferenceCar', 'alignmentSetStart', 'alignmentSetEnd',
                       'referenceAutoCalibration']
            for role in ('current', 'reference'):
                removed.extend(role + suffix for suffix in ('Segment', 'SaveLapRange', 'FindCrossing', 'EditContext'))
            for control in removed:
                assert await page.locator('#' + control).count() == 0, control
            checks.append('Redundant navigation, calibration, labels and manual-position controls are absent')

            await page.get_by_role('button', name='Credits', exact=True).click()
            await expect(page.locator('#view-credits')).to_be_visible()
            await expect(page.locator('#view-credits')).to_contain_text('Rice Formula SAE Club')
            assert not set(requests).intersection({'/api/status', '/api/history', '/api/network', '/api/frame', '/ws'})
            assert not errors, errors
            assert not fixture.unexpected, fixture.unexpected
            checks.append('No live telemetry/status/network transport requested')
            await page.screenshot(path=str(output / 'credits.png'), full_page=True)
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
    report = {'checks': checks, 'imports': fixture.imports, 'page_errors': errors,
              'scope': 'Isolated UI fixtures; no user recording or network download changed.'}
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
