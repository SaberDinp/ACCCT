const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { loadSettings } = require('../settings.cjs');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'accc migration '));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const current = path.join(root, 'ACCC Telemetry');
  const old = path.join(root, 'Apex Telemetry');
  fs.mkdirSync(current);
  fs.mkdirSync(path.join(old, 'library/data'), { recursive: true });
  fs.writeFileSync(path.join(old, 'library/data/telemetry.sqlite3'), 'existing recordings');
  fs.writeFileSync(path.join(old, 'desktop-settings.json'), JSON.stringify({ port: 9123, bounds: { width: 1200, height: 800 } }));
  return { root, current, old };
}

test('renaming preserves an existing library in place and its saved port', t => {
  const { root, current, old } = fixture(t);
  const settings = loadSettings(current, root);
  assert.equal(settings.library, path.join(old, 'library'));
  assert.equal(settings.port, 9123);
  assert.equal(fs.readFileSync(path.join(old, 'library/data/telemetry.sqlite3'), 'utf8'), 'existing recordings');
  assert.equal(fs.existsSync(path.join(current, 'library')), false);
});

test('an existing renamed configuration takes precedence, including default library selection', t => {
  const { root, current } = fixture(t);
  fs.writeFileSync(path.join(current, 'desktop-settings.json'), '{}');
  assert.deepEqual(loadSettings(current, root), {});
});

test('isolated test/development profiles do not inherit real user recordings', t => {
  const { root, current } = fixture(t);
  assert.deepEqual(loadSettings(current, root, false), {});
});

test('an explicitly selected older library is preserved; missing libraries are ignored', t => {
  const { root, current, old } = fixture(t);
  const selected = path.join(root, 'Selected Library');
  fs.mkdirSync(path.join(selected, 'data'), { recursive: true });
  fs.writeFileSync(path.join(selected, 'data/telemetry.sqlite3'), 'saved');
  fs.writeFileSync(path.join(old, 'desktop-settings.json'), JSON.stringify({ library: selected }));
  assert.equal(loadSettings(current, root).library, selected);
  fs.unlinkSync(path.join(selected, 'data/telemetry.sqlite3'));
  assert.deepEqual(loadSettings(current, root), {});
});
