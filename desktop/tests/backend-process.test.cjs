const test = require('node:test');
const assert = require('node:assert/strict');
const { once } = require('node:events');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { BackendProcess, backendCommand, pythonLaunch } = require('../backend-process.cjs');

const fakeBackend = `
  const fs = require('node:fs');
  const path = require('node:path');
  const mode = process.argv[2];
  const dataRoot = process.argv[process.argv.indexOf('--data-root') + 1];
  if (mode === 'exit') process.exit(7);
  process.stdin.setEncoding('utf8');
  let input = '';
  process.stdin.on('data', data => {
    input += data;
    if (mode !== 'stubborn' && input.includes('shutdown\\n')) {
      fs.writeFileSync(path.join(dataRoot, 'stopped.txt'), input);
      process.exit(0);
    }
  });
  process.stdin.on('end', () => { if (mode !== 'stubborn') process.exit(0); });
  setInterval(() => {}, 1000);
  if (!['timeout', 'stubborn'].includes(mode)) {
    console.log('ordinary log output');
    console.log('null');
    console.log(JSON.stringify({type: 'ready', port: 12345, pid: process.pid + 1}));
    console.log(JSON.stringify({type: 'ready', port: 0, pid: process.pid}));
    setTimeout(() => console.log(JSON.stringify({
      type: 'ready', port: 12345, pid: process.pid,
      cwd: process.cwd(), argv: process.argv,
      runtime: process.env.ACCC_JS_RUNTIME,
      asNode: process.env.ELECTRON_RUN_AS_NODE,
      pythonHome: process.env.PYTHONHOME, pythonPath: process.env.PYTHONPATH,
      pythonLauncher: process.env.__PYVENV_LAUNCHER__
    })), 10);
    if (mode === 'crash') setTimeout(() => process.exit(5), 100);
  }
`;

async function fixture(t, mode = 'ready', extra = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'ACCC desktop test '));
  const script = path.join(directory, 'fake-backend.cjs');
  await fs.writeFile(script, fakeBackend);
  const chunks = [];
  const backend = new BackendProcess([process.execPath, script, mode], {
    dataRoot: directory, log: { write: value => chunks.push(String(value)) },
    runtime: path.join(directory, 'ACCC Telemetry'), shutdownTimeout: 500, ...extra
  });
  t.after(async () => { await backend.stop(); await fs.rm(directory, { recursive: true, force: true }); });
  return { backend, directory, chunks };
}

test('source and packaged commands keep executable paths with spaces as separate arguments', () => {
  const projectRoot = path.join(os.tmpdir(), 'ACCC Source');
  const resourcesPath = path.join(os.tmpdir(), 'ACCC Telemetry', 'resources');
  assert.deepEqual(backendCommand({ packaged: false, platform: 'darwin', projectRoot }), [
    path.join(projectRoot, '.venv/bin/python'), path.join(projectRoot, 'desktop/backend.py')]);
  assert.deepEqual(backendCommand({ packaged: false, platform: 'win32', projectRoot, python: 'custom python' }), [
    'custom python', path.join(projectRoot, 'desktop/backend.py')]);
  assert.deepEqual(backendCommand({ packaged: true, platform: 'darwin', resourcesPath }), [
    path.join(resourcesPath, 'backend/accc-backend')]);
  assert.deepEqual(backendCommand({ packaged: true, platform: 'win32', resourcesPath }), [
    path.join(resourcesPath, 'backend/python.exe'), path.join(resourcesPath, 'backend/app/desktop/backend.py')]);
  assert.deepEqual(backendCommand({ packaged: true, platform: 'linux', resourcesPath }), [
    path.join(resourcesPath, 'backend/python/bin/python3'), path.join(resourcesPath, 'backend/app/desktop/backend.py')]);
});

test('Windows venv launches its base interpreter directly while preserving its environment identity', () => {
  const executable = 'C:\\ACCC Source\\.venv\\Scripts\\python.exe';
  const base = 'C:\\Program Files\\Python314\\python.exe';
  const config = 'C:\\ACCC Source\\.venv\\pyvenv.cfg';
  const files = {
    existsSync: value => [config, base].includes(value),
    readFileSync: value => { assert.equal(value, config); return 'home = C:\\Program Files\\Python314\r\ninclude-system-site-packages = false\r\n'; }
  };
  const command = [executable, 'C:\\ACCC Source\\desktop\\backend.py'];
  assert.deepEqual(pythonLaunch(command, 'win32', files), {
    command: [base, command[1]], launcher: executable
  });
  assert.deepEqual(pythonLaunch(command, 'darwin', files), { command });
  const packaged = ['C:\\ACCC Telemetry\\resources\\backend\\python.exe', 'app\\desktop\\backend.py'];
  assert.deepEqual(pythonLaunch(packaged, 'win32', files), { command: packaged });
});

test('an incomplete Windows venv fails clearly instead of accepting an unrelated process', () => {
  const command = ['C:\\ACCC Source\\.venv\\Scripts\\python.exe', 'backend.py'];
  assert.throws(() => pythonLaunch(command, 'win32', {
    existsSync: () => true, readFileSync: () => 'home = relative\\Python\n'
  }), /absolute base-interpreter/);
  assert.throws(() => pythonLaunch(command, 'win32', {
    existsSync: value => value.endsWith('pyvenv.cfg'),
    readFileSync: () => 'home = C:\\Missing Python\n'
  }), /base interpreter is missing/);
});

test('readiness belongs to the spawned process and shutdown is graceful and idempotent', async t => {
  const { backend, directory, chunks } = await fixture(t, 'ready', { port: 27182 });
  const oldHome = process.env.PYTHONHOME, oldPath = process.env.PYTHONPATH, oldLauncher = process.env.__PYVENV_LAUNCHER__;
  process.env.PYTHONHOME = '/incorrect/home';
  process.env.PYTHONPATH = '/incorrect/path';
  process.env.__PYVENV_LAUNCHER__ = '/incorrect/launcher';
  let readiness;
  try { readiness = backend.start(3000); }
  finally {
    if (oldHome === undefined) delete process.env.PYTHONHOME; else process.env.PYTHONHOME = oldHome;
    if (oldPath === undefined) delete process.env.PYTHONPATH; else process.env.PYTHONPATH = oldPath;
    if (oldLauncher === undefined) delete process.env.__PYVENV_LAUNCHER__; else process.env.__PYVENV_LAUNCHER__ = oldLauncher;
  }
  const ready = await readiness;
  assert.equal(ready.url, 'http://127.0.0.1:12345');
  assert.equal(ready.pid, backend.child.pid);
  assert.equal(await fs.realpath(ready.cwd), await fs.realpath(directory));
  assert.equal(ready.argv[ready.argv.indexOf('--data-root') + 1], directory);
  assert.equal(ready.argv[ready.argv.indexOf('--port') + 1], '27182');
  assert.equal(ready.argv[ready.argv.indexOf('--host') + 1], '127.0.0.1');
  assert.equal(ready.runtime, `node:${path.join(directory, 'ACCC Telemetry')}`);
  assert.equal(ready.asNode, '1');
  assert.equal(ready.pythonHome, undefined);
  assert.equal(ready.pythonPath, undefined);
  assert.equal(ready.pythonLauncher, undefined);
  assert.match(chunks.join(''), /ordinary log output/);
  await assert.rejects(backend.start(), /already been started/);
  const stopping = backend.stop();
  assert.strictEqual(backend.stop(), stopping);
  await stopping;
  assert.equal(await fs.readFile(path.join(directory, 'stopped.txt'), 'utf8'), 'shutdown\n');
  assert.equal(backend.child.exitCode, 0);
});

test('early child exit rejects startup', async t => {
  const { backend } = await fixture(t, 'exit');
  await assert.rejects(backend.start(3000), /code 7/);
  assert.equal(backend.dead, true);
});

test('missing executable rejects without leaving shutdown pending', async t => {
  const { backend, directory } = await fixture(t);
  backend.command = [path.join(directory, 'does-not-exist')];
  await assert.rejects(backend.start(3000), { code: 'ENOENT' });
  await backend.stop();
  assert.equal(backend.dead, true);
});

test('startup timeout stops its child even before the caller quits', async t => {
  const { backend, directory } = await fixture(t, 'timeout');
  await assert.rejects(backend.start(150), /0.15 seconds/);
  await backend.stop();
  assert.equal(backend.dead, true);
  assert.equal(await fs.readFile(path.join(directory, 'stopped.txt'), 'utf8'), 'shutdown\n');
});

test('a child that ignores shutdown is terminated after the grace period', async t => {
  const { backend } = await fixture(t, 'stubborn', { shutdownTimeout: 50 });
  await assert.rejects(backend.start(150), /did not start/);
  await backend.stop();
  assert.equal(backend.dead, true);
  assert.ok(backend.child.signalCode || backend.child.exitCode !== 0);
});

test('a running child crash is reported with its exit code', async t => {
  const { backend } = await fixture(t, 'crash');
  const crashed = once(backend, 'unexpected-exit');
  await backend.start(3000);
  const [event] = await crashed;
  assert.equal(event.code, 5);
});
