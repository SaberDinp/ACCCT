const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { PreferenceStore, installPreferenceHandler } = require('../preferences.cjs');

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ACCC preferences '));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const filename = path.join(directory, 'analysis-preferences.json');
  return { filename, store: new PreferenceStore(filename) };
}

test('analysis choices persist across process and origin changes without touching desktop settings', t => {
  const { filename, store } = fixture(t);
  const comparison = JSON.stringify({ current: { id: 'first' }, reference: { id: 'second' }, axis: 'elapsed' });
  const track = JSON.stringify({ track: 'spa', origin: .15, selectionKey: '3–4' });
  store.setItem('accc.comparison.v1', comparison);
  store.setItem('accc.track-review.v2.first:second', track);
  const reopened = new PreferenceStore(filename);
  assert.equal(reopened.getItem('accc.comparison.v1'), comparison);
  assert.equal(reopened.getItem('accc.track-review.v2.first:second'), track);
  assert.equal(reopened.getItem('accc.track-review.v2.other:'), null);
  assert.deepEqual(fs.readdirSync(path.dirname(filename)), ['analysis-preferences.json']);
});

test('only bounded JSON objects under the two analysis key namespaces can be stored', t => {
  const { store } = fixture(t);
  for (const key of ['../desktop-settings.json', 'library', '__proto__', 'accc.track-review.v2../outside', 'apex.comparison.v1']) {
    assert.throws(() => store.setItem(key, '{}'));
    assert.throws(() => store.getItem(key));
  }
  for (const value of ['null', '[]', 'invalid', '{}'.repeat(40000), { valid: true }]) {
    assert.throws(() => store.setItem('accc.comparison.v1', value));
  }
  assert.equal(store.getItem('accc.comparison.v1'), null);
});

test('IPC rejects other windows, child frames, untrusted origins, and unsupported operations', t => {
  const { store } = fixture(t);
  const ipc = new EventEmitter();
  const frame = { url: 'http://127.0.0.1:12345/' };
  const contents = { mainFrame: frame };
  installPreferenceHandler(ipc, { getWindow: () => ({ webContents: contents }), store,
    trusted: url => url === frame.url && url === 'http://127.0.0.1:12345/' });
  const call = (event, operation = 'set', key = 'accc.comparison.v1') => {
    ipc.emit('accc:analysis-preferences', event, operation, key, '{}');
    return event.returnValue;
  };
  assert.equal(call({ sender: {}, senderFrame: frame }).ok, false);
  assert.equal(call({ sender: contents, senderFrame: { ...frame } }).ok, false);
  frame.url = 'https://untrusted.example/';
  assert.equal(call({ sender: contents, senderFrame: frame }).ok, false);
  frame.url = 'http://127.0.0.1:12345/';
  assert.equal(call({ sender: contents, senderFrame: frame }, 'delete').ok, false);
  assert.equal(call({ sender: contents, senderFrame: frame }, 'set', '../outside').ok, false);
  assert.equal(store.getItem('accc.comparison.v1'), null);
  assert.equal(call({ sender: contents, senderFrame: frame }).ok, true);
  assert.deepEqual(call({ sender: contents, senderFrame: frame }, 'get'), { ok: true, value: '{}' });
});
