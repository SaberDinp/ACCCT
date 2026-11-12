/* Exercise the installed app with an isolated library and an attributed test clip. */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');

async function main() {
  const executablePath = process.argv[2];
  if (!executablePath) throw new Error('Usage: node desktop/check_desktop.cjs <packaged executable>');
  const video = path.resolve(process.env.ACCC_SMOKE_VIDEO || 'artifacts/desktop-package/sample.mp4');
  if (!fs.existsSync(video)) throw new Error('Create the test video with desktop/create_smoke_video.py, or set ACCC_SMOKE_VIDEO.');
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'accc desktop test '));
  const output = path.resolve(process.env.ACCC_SMOKE_OUTPUT || path.join(__dirname, '../artifacts/desktop-package'));
  fs.mkdirSync(output, { recursive: true });
  const env = { ...process.env, ACCC_DESKTOP_USER_DATA: path.join(temporary, 'preferences'),
    ACCC_DATA_ROOT: path.join(temporary, 'library') };
  delete env.ELECTRON_RUN_AS_NODE;
  const report = { executable: path.resolve(executablePath), launches: [] };
  const sessionIds = {};
  let occupiedPort, occupiedServer, unexpectedConnections = 0;
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      console.log(`Checking packaged launch ${attempt + 1}…`);
      let application, origin;
      const errors = [];
      try {
        application = await electron.launch({ executablePath: path.resolve(executablePath), env, timeout: 240000 });
        const page = await application.firstWindow();
        page.setDefaultTimeout(30000);
        page.on('pageerror', error => errors.push(error.message));
        await page.waitForURL('http://127.0.0.1:*/', { timeout: 180000 });
        await page.locator('.main-tabs').waitFor();
        await page.waitForFunction(() => !document.getElementById('currentSession')?.disabled);
        origin = new URL(page.url()).origin;
        const settings = JSON.parse(fs.readFileSync(path.join(env.ACCC_DESKTOP_USER_DATA, 'desktop-settings.json')));
        assert.equal(settings.port, Number(new URL(origin).port));
        const desktop = await application.evaluate(({ app, BrowserWindow }) => {
          const prefs = BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences();
          return { packaged: app.isPackaged, userData: app.getPath('userData'), version: app.getVersion(),
            nodeIntegration: prefs.nodeIntegration, contextIsolation: prefs.contextIsolation, sandbox: prefs.sandbox };
        });
        assert.equal(desktop.packaged, true);
        assert.equal(await page.title(), 'ACCC Telemetry');
        assert.equal(fs.realpathSync(desktop.userData), fs.realpathSync(env.ACCC_DESKTOP_USER_DATA));
        assert.equal(desktop.nodeIntegration, false);
        assert.equal(desktop.contextIsolation, true);
        assert.equal(desktop.sandbox, true);
        assert.equal(await page.evaluate(() => typeof window.require), 'undefined');
        assert.equal(await page.evaluate(() => typeof window.ACCCDesktopPreferences?.getItem), 'function');
        assert.equal(await page.evaluate(() => {
          try { window.ACCCDesktopPreferences.setItem('../desktop-settings.json', '{}'); return false; }
          catch { return true; }
        }), true, 'The preload must reject arbitrary storage keys.');
        if (attempt > 0) {
          assert.notEqual(origin, report.launches[0].origin, 'Relaunch must exercise a different browser origin.');
          assert.notEqual(Number(new URL(origin).port), occupiedPort, 'The app must not reuse another listener.');
        }
        assert.deepEqual((await page.locator('.main-tabs button').allTextContents()).map(value => value.trim()), ['Analysis', 'Credits']);
        assert.equal(await page.locator('#view-analysis').isVisible(), true);
        assert.equal(await page.locator('#view-monitor').count(), 0);
        const api = async endpoint => {
          const response = await fetch(origin + endpoint);
          assert.equal(response.status, 200, endpoint);
          return response.json();
        };
        await api('/api/catalog');
        await api('/api/tracks');
        await api('/api/validation');
        for (const endpoint of ['/phone', '/api/network', '/api/qr']) {
          assert.equal((await fetch(origin + endpoint)).status, 404, endpoint);
        }
        if (attempt === 0) {
          assert.deepEqual(await api('/api/sessions'), []);
          for (const role of ['current', 'reference']) {
            console.log(`Importing ${role} recording…`);
            await page.locator('#importRecording').click();
            await page.locator('#referenceImportDialog').waitFor({ state: 'visible' });
            await page.locator('#recordingImportRole').selectOption(role);
            await page.locator('#referenceName').fill(`Desktop test ${role}`);
            await page.locator('#referenceUploadInput').setInputFiles(video);
            await page.waitForFunction(() => document.getElementById('referenceLocalFile').value.replaceAll('\\', '/').startsWith('data/uploads/'));
            await page.locator('#startReferenceImport').click();
            await page.locator('#referenceImportDialog').waitFor({ state: 'hidden' });
            const deadline = Date.now() + 120000;
            let imported;
            while (Date.now() < deadline) {
              const jobs = await api('/api/imports');
              const job = jobs.find(item => item.role === role);
              if (job?.state === 'error') throw new Error(job.error);
              if (job?.state === 'completed' && job.session_id) { imported = job; break; }
              await new Promise(resolve => setTimeout(resolve, 250));
            }
            assert.ok(imported, `Timed out importing ${role}`);
            sessionIds[role] = imported.session_id;
            await page.waitForFunction(({ role, id }) => document.getElementById(role + 'Session').value === id,
              { role, id: sessionIds[role] });
            const recording = await api(`/api/sessions/${sessionIds[role]}`);
            assert.ok(recording.samples.some(sample => sample.hud_visible && sample.speed > 0), 'Bundled extractor must read the test HUD.');
          }
        } else {
          for (const role of ['current', 'reference']) {
            await page.waitForFunction(({ role, id }) => document.getElementById(role + 'Session').value === id,
              { role, id: sessionIds[role] });
          }
        }
        assert.equal((await api('/api/sessions')).length, 2);
        await page.waitForFunction(() => document.getElementById('comparisonValues').textContent.includes('Source'));
        await page.waitForFunction(ids => !document.getElementById('trackReviewPlay').disabled &&
          ['current', 'reference'].every(role => {
            const video = document.getElementById('track' + role + 'Native');
            return video?.videoWidth > 0 && !video.hidden && video.currentSrc.includes(`/api/sessions/${ids[role]}/`);
          }), sessionIds, { timeout: 60000 });
        if (attempt === 0) await page.locator('#trackSelect').selectOption('silverstone');
        else await page.waitForFunction(() => document.getElementById('trackSelect').value === 'silverstone');
        await page.waitForFunction(ids => document.getElementById('trackAnnotations').dataset.trackId === 'silverstone' &&
          JSON.parse(window.ACCCDesktopPreferences.getItem(`accc.track-review.v2.${ids.current}:${ids.reference}`) || '{}').track === 'silverstone', sessionIds);
        await page.screenshot({ path: path.join(output, `desktop-analysis-${attempt + 1}.png`), fullPage: false });

        console.log('Checking recording library and CSV export…');
        await page.locator('.main-tabs [data-view="analysis"]').click();
        await page.getByRole('button', { name: 'Recordings', exact: true }).click();
        await page.locator('#view-sessions').waitFor({ state: 'visible' });
        await application.evaluate(({ session }, savePath) => {
          globalThis.acccTestDownload = new Promise(resolve => {
            session.defaultSession.once('will-download', (_event, item) => {
              item.setSavePath(savePath);
              item.once('done', (_doneEvent, state) => resolve(state));
            });
          });
        }, path.join(output, 'session.csv'));
        const row = page.locator('tr').filter({ has: page.locator(`.session-open[data-id="${sessionIds.current}"]`) });
        await row.getByRole('link', { name: 'Export CSV', exact: true }).click();
        assert.equal(await application.evaluate(() => Promise.race([
          globalThis.acccTestDownload, new Promise(resolve => setTimeout(() => resolve('timeout'), 15000))
        ])), 'completed');
        assert.ok(fs.readFileSync(path.join(output, 'session.csv'), 'utf8').startsWith('source_time_s'));
        await page.locator('.main-tabs [data-view="analysis"]').click();
        await page.getByRole('button', { name: 'Calibration', exact: true }).click();
        await page.locator('#view-calibration').waitFor({ state: 'visible' });
        console.log('Checking credits, copy and external donation link…');
        await page.locator('.main-tabs [data-view="credits"]').click();
        await page.locator('#creditsTitle').waitFor({ state: 'visible' });
        assert.match(await page.locator('#creditsTitle').innerText(), /Rice Racing/);
        assert.equal(await page.locator('#riceRacingClubName').textContent(), 'Rice Formula SAE Club');
        await page.waitForFunction(() => document.querySelector('.rr-team-photo img').naturalWidth > 0);
        const previousClipboard = await application.evaluate(({ clipboard, shell, app }) => {
          app.acccTestExternalLinks = [];
          shell.openExternal = async url => { app.acccTestExternalLinks.push(url); };
          return clipboard.readText() || '';
        });
        try {
          await page.locator('#copyRiceRacingName').click();
          await page.waitForFunction(() => document.getElementById('riceRacingCopyStatus').textContent.startsWith('Copied.'));
          assert.equal(await application.evaluate(({ clipboard }) => clipboard.readText()), 'Rice Formula SAE Club');
          console.log('Donation designation copied successfully.');
          await page.locator('.rr-donate').click();
          assert.deepEqual(await application.evaluate(({ app }) => app.acccTestExternalLinks), ['https://giving.rice.edu/']);
        } finally {
          await application.evaluate(({ clipboard }, previous) => clipboard.writeText(previous), previousClipboard);
        }
        await page.locator('#creditsTitle').scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(output, `desktop-credits-${attempt + 1}.png`), fullPage: false });
        assert.deepEqual(errors, []);
        report.launches.push({ ...desktop, origin, recordedImports: 'passed', comparison: 'passed', videoPlayback: 'passed',
          csvExport: 'passed', persistence: 'passed', credits: 'passed', donationCopyAndLink: 'passed', javascriptErrors: errors });
      } catch (error) {
        for (const filename of ['desktop-settings.json', 'analysis-preferences.json']) {
          const source = path.join(env.ACCC_DESKTOP_USER_DATA, filename);
          if (fs.existsSync(source)) fs.copyFileSync(source, path.join(output, `failure-${filename}`));
        }
        if (application) {
          const page = application.windows()[0];
          if (page) await page.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {});
        }
        throw error;
      } finally {
        if (application) {
          let timer;
          try {
            await Promise.race([application.close(), new Promise((_, reject) => {
              timer = setTimeout(() => { application.process().kill('SIGKILL'); reject(new Error('Desktop shutdown timed out')); }, 45000);
            })]);
          } finally { clearTimeout(timer); }
        }
      }
      if (origin) {
        await assert.rejects(fetch(origin + '/api/status'), 'Backend should stop when desktop closes');
        report.launches.at(-1).shutdown = 'passed';
      }
      if (attempt === 0) {
        // Reserve a fresh port rather than relying on Windows TIME_WAIT timing.
        // Point the saved configuration at it to force a safe port fallback.
        occupiedServer = net.createServer(socket => { unexpectedConnections++; socket.destroy(); });
        await new Promise((resolve, reject) => {
          occupiedServer.once('error', reject);
          occupiedServer.listen(0, '127.0.0.1', resolve);
        });
        occupiedPort = occupiedServer.address().port;
        const filename = path.join(env.ACCC_DESKTOP_USER_DATA, 'desktop-settings.json');
        const settings = JSON.parse(fs.readFileSync(filename, 'utf8'));
        fs.writeFileSync(filename, JSON.stringify({ ...settings, port: occupiedPort }));
      }
    }
    assert.equal(unexpectedConnections, 0, 'The app must never contact an unrelated listener on its saved port.');
    report.occupiedPortFallback = 'comparison selections and circuit restored on a new origin';
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report, null, 2));
  } finally {
    if (occupiedServer) await new Promise(resolve => occupiedServer.close(resolve));
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
