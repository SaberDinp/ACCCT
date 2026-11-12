const fs = require('node:fs');
const path = require('node:path');

const CHANNEL = 'accc:analysis-preferences';
const MAX_VALUE_BYTES = 64 * 1024;
const MAX_STORE_BYTES = 4 * 1024 * 1024;
const allowedKey = key => typeof key === 'string' && (key === 'accc.comparison.v1' ||
  /^accc\.track-review\.v2\.[A-Za-z0-9_-]{0,160}:[A-Za-z0-9_-]{0,160}$/.test(key));
function validValue(value) {
  if (typeof value !== 'string' || Buffer.byteLength(value) > MAX_VALUE_BYTES) return false;
  try {
    const parsed = JSON.parse(value);
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed);
  } catch { return false; }
}

// These are UI choices only. The renderer never supplies a filesystem path.
class PreferenceStore {
  constructor(filename) {
    this.filename = filename;
    this.values = {};
    try {
      if (fs.statSync(filename).size > MAX_STORE_BYTES) return;
      const stored = JSON.parse(fs.readFileSync(filename, 'utf8'));
      for (const [key, value] of Object.entries(stored || {})) {
        if (allowedKey(key) && validValue(value)) this.values[key] = value;
      }
    } catch { /* A missing or invalid preference file starts with defaults. */ }
  }

  getItem(key) {
    if (!allowedKey(key)) throw new Error('Unsupported analysis preference.');
    return this.values[key] ?? null;
  }

  setItem(key, value) {
    if (!allowedKey(key) || !validValue(value)) throw new Error('Invalid analysis preference.');
    if (this.values[key] === value) return;
    const next = { ...this.values, [key]: value };
    const content = JSON.stringify(next);
    if (Buffer.byteLength(content) > MAX_STORE_BYTES) throw new Error('Analysis preferences are full.');
    fs.mkdirSync(path.dirname(this.filename), { recursive: true });
    const temporary = `${this.filename}.tmp`;
    try {
      fs.writeFileSync(temporary, content, { encoding: 'utf8', mode: 0o600 });
      fs.renameSync(temporary, this.filename);
      this.values = next;
    } finally {
      fs.rmSync(temporary, { force: true });
    }
  }
}

function installPreferenceHandler(ipcMain, { getWindow, trusted, store, onError = () => {} }) {
  ipcMain.on(CHANNEL, (event, operation, key, value) => {
    event.returnValue = { ok: false };
    const contents = getWindow()?.webContents;
    if (!contents || event.sender !== contents || event.senderFrame !== contents.mainFrame ||
        !trusted(event.senderFrame?.url) || !allowedKey(key)) return;
    try {
      if (operation === 'get') event.returnValue = { ok: true, value: store.getItem(key) };
      else if (operation === 'set') {
        store.setItem(key, value);
        event.returnValue = { ok: true };
      }
    } catch (error) { onError(error); }
  });
}

module.exports = { PreferenceStore, installPreferenceHandler };
