"""Isolated export-button checks using fixture comparisons and local downloads.

Only static assets are served. API/WebSocket fixtures leave the user's library,
source controls, and browser preferences untouched.
"""
import asyncio
import copy
import functools
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

from check_delete_swap_browser import CURRENT, REFERENCE, Fixtures
from browser_fixtures import ROOT, SOCKET_FIXTURE, StaticHandler, lock_lap_boundary
from telemetry.comparison import compare_session_ranges


OUT = ROOT / 'artifacts/comparison-export-browser'


class ExportFixtures(Fixtures):
    def __init__(self):
        super().__init__()
        self.fail_compare = False
        self.fail_session = None
        self.comparisons = []

    async def route(self, route):
        request = route.request
        path = urlparse(request.url).path
        if self.fail_session and path == '/api/sessions/' + self.fail_session:
            self.requests.append((request.method, path))
            return await route.fulfill(status=500, json={'detail': 'Fixture recording load failed.'})
        if path != '/api/compare' or request.method != 'POST':
            return await super().route(route)
        self.requests.append((request.method, path))
        if self.fail_compare:
            return await route.fulfill(status=500, json={'detail': 'Fixture comparison failed.'})
        body = request.post_data_json
        try:
            result = compare_session_ranges(self.sessions[body['reference_session_id']]['samples'],
                self.sessions[body['current_session_id']]['samples'], reference_range=body['reference_range'],
                current_range=body['current_range'], axis=body['axis'], align_start=body['align_start'],
                align_end=body['align_end'], alignment_mode=body['alignment_mode'], landmarks=body['landmarks'])
            result['sessions'] = {side: copy.deepcopy(self.sessions[body[side + '_session_id']]['session'])
                                  for side in ('current', 'reference')}
        except ValueError as error:
            return await route.fulfill(status=400, json={'detail': str(error)})
        gate, self.next_compare = self.next_compare, None
        if gate:
            await gate.wait()
        self.comparisons.append(copy.deepcopy(result))
        await route.fulfill(json=result)
        if gate:
            gate.finished.set()


async def download(page, fixture, label):
    before = len([method for method, _ in fixture.requests if method == 'POST'])
    async with page.expect_download() as pending:
        await page.get_by_role('button', name='Export for AI', exact=True).click()
    artifact = await pending.value
    path = OUT / (label + '-' + artifact.suggested_filename)
    await artifact.save_as(path)
    payload = path.read_bytes()
    document = json.loads(payload)
    assert document['format'] == 'accc-telemetry-comparison' and document['schema_version'] == 1
    assert artifact.suggested_filename.endswith('.json')
    assert 0 < len(payload) <= 200 * 1024
    assert len([method for method, _ in fixture.requests if method == 'POST']) == before
    output = await page.evaluate('window.exportOutputs.at(-1)')
    assert output['bytes'] == len(payload) and output['rowCount'] <= 600, output
    assert len(document['telemetry']['rows']) == output['rowCount']
    snapshot = await page.evaluate('window.exportInputs.at(-1)')
    assert document['comparison']['axis'] == snapshot['axis']
    for side in ('current', 'reference'):
        assert document['runs'][side]['selection']['start_source_s'] == snapshot[side]['selection']['start_time']
        assert document['runs'][side]['selection']['end_source_s'] == snapshot[side]['selection']['end_time']
    await expect(page.locator('#noticeText')).to_contain_text(artifact.suggested_filename)
    return {'filename': artifact.suggested_filename, 'bytes': len(payload), 'rows': output['rowCount']}


async def main():
    OUT.mkdir(exist_ok=True, parents=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(StaticHandler, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fixture = ExportFixtures()
    original = copy.deepcopy(fixture.sessions)
    initial_load = fixture.gate('session', CURRENT)
    checks, errors, downloads = {}, [], []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(viewport={'width': 1440, 'height': 1100}, accept_downloads=True)
            await context.route('**/api/**', fixture.route)
            await context.add_init_script(SOCKET_FIXTURE)
            preferences = {'axis': 'distance', 'alignment_mode': 'raw',
                'current': {'id': CURRENT, 'range': 'custom', 'start': 10, 'end': 110, 'segment': '1'},
                'reference': {'id': REFERENCE, 'range': 'custom', 'start': 12, 'end': 110, 'segment': '1'}}
            await context.add_init_script('localStorage.setItem("accc.comparison.v1", %s)' % json.dumps(json.dumps(preferences)))
            page = await context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(f'http://127.0.0.1:{server.server_port}')
            await page.get_by_role('button', name='Analysis', exact=True).click()
            await asyncio.wait_for(initial_load.arrived.wait(), 20)
            button = page.get_by_role('button', name='Export for AI', exact=True)
            await expect(button).to_be_disabled()
            initial_load.release.set()
            await expect(button).to_be_enabled(timeout=20000)
            checks['disabled_during_recording_load'] = True
            await page.evaluate('window.scrollTo(0, 0)')
            await expect(button).to_be_in_viewport()
            checks['desktop_export_visible_without_scrolling'] = True
            await page.evaluate('''() => {
                window.exportInputs = []; window.exportOutputs = [];
                const formatter = window.ACCCComparisonExport;
                window.ACCCComparisonExport = {...formatter, create: result => {
                    window.exportInputs.push(JSON.parse(JSON.stringify(result)));
                    const output = formatter.create(result);
                    window.exportOutputs.push({bytes:output.bytes,rowCount:output.rowCount});
                    return output;
                }};
            }''')
            await page.locator('#compareZoomIn').click()
            downloads.append(await download(page, fixture, 'initial'))
            snapshot = await page.evaluate('window.exportInputs.at(-1)')
            assert snapshot['extent'] == fixture.comparisons[-1]['extent']
            assert len(snapshot['points']) == len(fixture.comparisons[-1]['points'])
            assert snapshot['current']['selection']['start_time'] == 10
            assert snapshot['reference']['selection']['start_time'] == 12
            checks['whole_selected_comparison_downloads_without_network'] = True

            # An old response cannot re-enable export after a later locked edit.
            gate = fixture.gate('compare')
            await lock_lap_boundary(page, 'current', 'End', '110.125')
            await asyncio.wait_for(gate.arrived.wait(), 20)
            await expect(button).to_be_disabled()
            later = fixture.gate('compare')
            await lock_lap_boundary(page, 'current', 'Start', '10.125')
            await asyncio.wait_for(later.arrived.wait(), 20)
            gate.release.set()
            await asyncio.wait_for(gate.finished.wait(), 20)
            await expect(button).to_be_disabled()
            later.release.set()
            await asyncio.wait_for(later.finished.wait(), 20)
            await expect(button).to_be_enabled(timeout=15000)
            downloads.append(await download(page, fixture, 'edited'))
            snapshot = await page.evaluate('window.exportInputs.at(-1)')
            assert snapshot['current']['selection']['start_time'] == 10.125
            assert snapshot['current']['selection']['end_time'] == 110.125
            checks['locked_edits_and_stale_inflight_response_blocked'] = True

            await page.locator('#currentStartBoundary').click()
            await page.locator('#alignmentTime').fill('150')
            await expect(page.locator('#alignmentLock')).to_be_enabled()
            await page.locator('#alignmentLock').click()
            await expect(page.locator('#alignmentError')).to_contain_text('Choose a start before the end of the lap')
            await expect(page.locator('#currentStart')).to_have_value('10.125')
            await page.locator('#closeAlignment').click()
            await expect(button).to_be_enabled()
            fixture.fail_compare = True
            await page.locator('#axisElapsed').click()
            await expect(page.locator('#comparisonStatus')).to_have_text('Fixture comparison failed.')
            await expect(button).to_be_disabled()
            fixture.fail_compare = False
            await page.locator('#runComparison').click()
            await expect(button).to_be_enabled(timeout=15000)
            checks['invalid_selection_and_failed_comparison_blocked'] = True

            await page.locator('#referenceSession').select_option('')
            await expect(button).to_be_disabled()
            await expect(page.locator('#comparisonStatus')).to_have_text('', timeout=15000)
            await expect(button).to_be_disabled()
            fixture.fail_session = REFERENCE
            await page.locator('#referenceSession').select_option(REFERENCE)
            await expect(page.locator('#referenceDetail')).to_have_text('Fixture recording load failed.')
            await expect(button).to_be_disabled()
            fixture.fail_session = None
            await page.locator('#referenceSession').select_option('')
            await page.locator('#referenceSession').select_option(REFERENCE)
            await expect(button).to_be_enabled(timeout=15000)
            checks['single_recording_and_load_failure_blocked'] = True

            await page.locator('#swapComparisonRuns').click()
            await expect(button).to_be_enabled(timeout=15000)
            downloads.append(await download(page, fixture, 'swapped'))
            snapshot = await page.evaluate('window.exportInputs.at(-1)')
            assert snapshot['sessions']['current']['id'] == REFERENCE
            assert snapshot['sessions']['reference']['id'] == CURRENT
            checks['export_tracks_current_roles_and_axis'] = snapshot['axis'] == 'elapsed'
            await page.set_viewport_size({'width': 390, 'height': 844})
            await page.evaluate('window.scrollTo(0, 0)')
            await expect(button).to_be_visible()
            await expect(button).to_be_in_viewport()
            assert not await page.evaluate('document.documentElement.scrollWidth > innerWidth + 1')
            await page.screenshot(path=str(OUT / 'export-mobile.png'), full_page=True)
            checks['mobile_export_control_visible_without_overflow'] = True
            assert fixture.sessions == original
            assert not fixture.unexpected and not errors, {'requests': fixture.unexpected, 'errors': errors}
            await browser.close()
        report = {'checks': checks, 'downloads': downloads, 'page_errors': errors, 'user_library_untouched': True}
        (OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        for gate in fixture.gates:
            gate.release.set()
        server.shutdown()


if __name__ == '__main__':
    asyncio.run(main())
