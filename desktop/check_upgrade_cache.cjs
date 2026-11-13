/* Verify an upgrade replaces cached UI assets without erasing saved choices.
 * Usage: node desktop/check_upgrade_cache.cjs <packaged executable> [--expect-stale]
 *        node desktop/check_upgrade_cache.cjs --source [--electron <Electron executable>]
 * Both launches use the same executable, including the installed app's sandbox.
 */
const { _electron: electron } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');

const args = process.argv.slice(2);
const source = args.includes('--source');
const expectStale = args.includes('--expect-stale');
const runtimeIndex = args.indexOf('--electron');
const runtime = source ? (runtimeIndex < 0 ? require('electron') : path.resolve(args[runtimeIndex + 1])) : null;
const executable = source ? runtime : path.resolve(args[0] || '');
if (!source && (!args[0] || args[0].startsWith('--'))) throw new Error('Provide a packaged executable or --source.');
const output = path.resolve(process.env.ACCC_UPGRADE_OUTPUT || path.join(__dirname, '../artifacts/upgrade-cache'));
const comparisonKey = 'accc.track-review.v2.cache-probe:cache-reference';
const savedChoice = JSON.stringify({ track: 'silverstone', upgradeCacheProbe: 'preserve-native-preference' });
const storageKey = 'accc.upgrade-cache.local-storage-probe';
const storageValue = 'preserve-existing-browser-storage';

async function close(application) {
  if (!application) return;
  let timer;
  try {
    await Promise.race([application.close(), new Promise((_, reject) => {
      timer = setTimeout(() => {
        application.process().kill('SIGKILL');
        reject(new Error('Upgrade-cache test app did not close within 45 seconds.'));
      }, 45000);
    })]);
  } finally { clearTimeout(timer); }
}

async function main() {
  fs.mkdirSync(output, { recursive: true });
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'accc upgrade cache '));
  const profile = path.join(temporary, 'profile');
  const library = path.join(temporary, 'library');
  fs.mkdirSync(profile, { recursive: true });
  fs.mkdirSync(path.join(library, 'data'), { recursive: true });
  fs.writeFileSync(path.join(profile, 'analysis-preferences.json'), JSON.stringify({ [comparisonKey]: savedChoice }));
  const librarySentinel = path.join(library, 'data', 'upgrade-cache-test.txt');
  fs.writeFileSync(librarySentinel, 'keep existing library files');
  const requests = [];
  let application;
  const server = http.createServer((request, response) => {
    requests.push(request.url);
    const files = {
      '/': ['text/html', '<!doctype html><html><head><title>Old Monitor cache fixture</title><link rel="stylesheet" href="/static/styles.css"><script src="/static/app.js" defer></script></head><body><nav class="main-tabs"><button data-view="monitor">Monitor</button><button data-view="analysis">Analysis</button></nav><section id="view-monitor">Old cached Monitor interface</section></body></html>'],
      '/static/styles.css': ['text/css', ':root { --upgrade-cache-probe: old-monitor-style; } body { background: #16191c; color: white; font: 18px sans-serif; }'],
      '/static/app.js': ['text/javascript', 'window.__ACCC_STALE_UI__ = "old-monitor-script";']
    };
    const entry = files[request.url];
    if (!entry) { response.writeHead(404); response.end(); return; }
    response.writeHead(200, { 'Content-Type': entry[0], 'Cache-Control': 'public, max-age=31536000',
      'Last-Modified': 'Mon, 01 Jan 2024 00:00:00 GMT', ETag: '"old-monitor-fixture"' });
    response.end(entry[1]);
  });
  const report = { executable, mode: expectStale ? 'unpatched-reproduction' : 'fixed-upgrade', source, requests };
  try {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const port = server.address().port;
    const origin = `http://127.0.0.1:${port}`;
    report.origin = origin;
    const settingsPath = path.join(profile, 'desktop-settings.json');
    fs.writeFileSync(settingsPath, JSON.stringify({ port: 0 }));
    const env = { ...process.env, ACCC_DESKTOP_USER_DATA: profile, ACCC_DATA_ROOT: library };
    delete env.ELECTRON_RUN_AS_NODE;
    application = await electron.launch({ executablePath: executable, args: source ? [__dirname] : [], env, timeout: 240000 });
    let page = await application.firstWindow();
    await page.waitForURL('http://127.0.0.1:*/', { timeout: 180000 });
    await page.locator('.main-tabs').waitFor();
    const seedBackendOrigin = new URL(page.url()).origin;
    assert.notEqual(seedBackendOrigin, origin);
    report.seedDesktop = await application.evaluate(({ app }) => ({
      executable: process.execPath, version: app.getVersion(), packaged: app.isPackaged,
      userData: app.getPath('userData')
    }));
    assert.equal(fs.realpathSync(report.seedDesktop.userData), fs.realpathSync(profile));
    // Programmatic navigation warms this installed runtime's actual cache.
    // The fixture makes no native-bridge calls and never accesses user data.
    await application.evaluate(async ({ BrowserWindow }, url) => {
      await BrowserWindow.getAllWindows()[0].loadURL(url);
    }, origin);
    await page.waitForURL(origin + '/', { timeout: 30000 });
    await page.waitForFunction(() => window.__ACCC_STALE_UI__ === 'old-monitor-script');
    assert.equal(await page.locator('#view-monitor').isVisible(), true);
    await page.evaluate(({ key, value }) => localStorage.setItem(key, value), { key: storageKey, value: storageValue });
    report.seedCacheBytes = await application.evaluate(async ({ session }) => {
      session.defaultSession.flushStorageData();
      return session.defaultSession.getCacheSize();
    });
    assert.ok(report.seedCacheBytes > 0);
    assert.ok(['/', '/static/styles.css', '/static/app.js'].every(url => requests.includes(url)));
    await close(application);
    application = null;
    await assert.rejects(fetch(seedBackendOrigin + '/api/status'), 'The seeding app must stop its backend.');
    const settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
    fs.writeFileSync(settingsPath, JSON.stringify({ ...settings, port }));
    await new Promise(resolve => server.close(resolve));
    console.log('Old HTML, JavaScript and CSS cached by the actual app in an isolated persistent profile.');

    application = await electron.launch({ executablePath: executable, args: source ? [__dirname] : [], env, timeout: 240000 });
    page = await application.firstWindow();
    page.setDefaultTimeout(30000);
    await page.waitForURL(origin + '/', { timeout: 180000 });
    await page.locator('.main-tabs').waitFor();
    report.desktop = await application.evaluate(({ app }) => ({
      version: app.getVersion(), packaged: app.isPackaged, userData: app.getPath('userData')
    }));
    assert.equal(fs.realpathSync(report.desktop.userData), fs.realpathSync(profile));
    report.navigation = (await page.locator('.main-tabs button').allTextContents()).map(text => text.trim());
    report.monitorViews = await page.locator('#view-monitor').count();
    report.browserState = await page.evaluate(key => ({
      stored: localStorage.getItem(key), staleScript: window.__ACCC_STALE_UI__ || null,
      staleStyle: getComputedStyle(document.documentElement).getPropertyValue('--upgrade-cache-probe').trim()
    }), storageKey);
    const networkHtml = await (await fetch(origin + '/')).text();
    assert.ok(!networkHtml.includes('view-monitor'), 'Backend must serve the current UI even during the stale-cache reproduction.');
    report.backendServesCurrentHtml = true;
    if (expectStale) {
      assert.deepEqual(report.navigation, ['Monitor', 'Analysis']);
      assert.equal(report.monitorViews, 1);
      assert.equal(report.browserState.staleScript, 'old-monitor-script');
      assert.equal(report.browserState.staleStyle, 'old-monitor-style');
    } else {
      assert.deepEqual(report.navigation, ['Analysis', 'Credits']);
      assert.equal(report.monitorViews, 0);
      assert.equal(report.browserState.staleScript, null);
      assert.equal(report.browserState.staleStyle, '');
      await page.locator('.main-tabs [data-view="credits"]').click();
      await page.locator('#creditsTitle').waitFor({ state: 'visible' });
      await page.waitForFunction(() => document.querySelector('.rr-team-photo img')?.naturalWidth > 0);
      assert.equal(await page.evaluate(key => window.ACCCDesktopPreferences.getItem(key), comparisonKey), savedChoice);
    }
    assert.equal(report.browserState.stored, storageValue);
    const preferences = JSON.parse(fs.readFileSync(path.join(profile, 'analysis-preferences.json'), 'utf8'));
    assert.equal(preferences[comparisonKey], savedChoice);
    assert.equal(fs.readFileSync(librarySentinel, 'utf8'), 'keep existing library files');
    report.preserved = { localStorage: true, nativeAnalysisPreferences: true, libraryFiles: true };
    await page.screenshot({ path: path.join(output, `${report.mode}.png`), fullPage: false });
    await close(application);
    application = null;
    await assert.rejects(fetch(origin + '/api/status'), 'Closing the desktop must stop its backend.');
    report.shutdown = 'passed';
    report.result = 'passed';
    fs.writeFileSync(path.join(output, `${report.mode}.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    report.error = error.stack;
    fs.writeFileSync(path.join(output, `${report.mode}-failure.json`), JSON.stringify(report, null, 2));
    throw error;
  } finally {
    await close(application);
    if (server.listening) await new Promise(resolve => server.close(resolve));
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
