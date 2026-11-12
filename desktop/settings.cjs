const fs = require('node:fs');
const path = require('node:path');

function readObject(filename) {
  try {
    const value = JSON.parse(fs.readFileSync(filename, 'utf8'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

function loadSettings(userData, appData, allowMigration = true) {
  const current = path.join(userData, 'desktop-settings.json');
  if (fs.existsSync(current)) return readObject(current);
  if (!allowMigration) return {};
  // Keep old recordings in place: existing database entries contain absolute paths.
  const legacy = path.join(appData, 'Apex Telemetry');
  const previous = readObject(path.join(legacy, 'desktop-settings.json'));
  const oldLibrary = typeof previous.library === 'string' ? previous.library : path.join(legacy, 'library');
  if (fs.existsSync(path.join(oldLibrary, 'data', 'telemetry.sqlite3'))) previous.library = oldLibrary;
  else delete previous.library;
  return previous;
}

module.exports = { loadSettings };
