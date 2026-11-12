const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const { patchSource, prepare, MARKER } = require('../prepare_linux_packaging.cjs');

test('AppImage preparation rejects unknown dependency versions and template changes', () => {
  assert.throws(() => patchSource('anything', '27.0.0'), /Review the AppImage launcher/);
  assert.throws(() => patchSource('anything', '26.18.0'), /template changed/);
  assert.throws(() => patchSource(`${MARKER}\nNO_SANDBOX=(--no-sandbox)`, '26.18.0'), /fallback unexpectedly remains/);
});

test('generated AppImage launcher does not add --no-sandbox when user namespaces are unavailable', {
  skip: process.platform === 'win32' ? 'AppRun is a POSIX shell script; exercised by Linux/macOS jobs' : false
}, () => {
  prepare();
  const source = fs.readFileSync(require.resolve('app-builder-lib/out/targets/appimage/appImageUtil.js'), 'utf8');
  assert.equal(patchSource(source, '26.18.0'), source);
  const { generateAppRunScript } = require('app-builder-lib/out/targets/appimage/appImageUtil.js');
  const script = generateAppRunScript({ ExecutableName: 'accc-test', ProductName: 'ACCC Telemetry' });
  assert.ok(script.includes(MARKER));
  assert.ok(!script.includes('--no-sandbox'));
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'accc launcher '));
  try {
    fs.writeFileSync(path.join(directory, 'AppRun'), script, { mode: 0o755 });
    fs.writeFileSync(path.join(directory, 'unshare'), '#!/bin/sh\nexit 1\n', { mode: 0o755 });
    fs.writeFileSync(path.join(directory, 'accc-test'), '#!/bin/sh\nprintf "%s\\n" "$@"\n', { mode: 0o755 });
    const result = spawnSync('bash', [path.join(directory, 'AppRun'), 'recording with spaces.mp4'], {
      env: { ...process.env, APPDIR: directory, APPIMAGE_EXIT_AFTER_INSTALL: '',
        PATH: `${directory}${path.delimiter}${process.env.PATH}` }, encoding: 'utf8', timeout: 10000
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), 'recording with spaces.mp4');
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});
