/* electron-builder 26.18.0 adds --no-sandbox if its unshare probe fails.
 * Keep Chromium's sandbox requirement for ACCC Telemetry. This narrow, checked
 * build-time patch is reapplied after npm ci; it never changes the runtime UI.
 */
const fs = require('node:fs');
const path = require('node:path');

const SUPPORTED_VERSION = '26.18.0';
const MARKER = '# ACCC Telemetry: never automatically disable the Chromium sandbox.';

function patchSource(source, version) {
  if (version !== SUPPORTED_VERSION) {
    throw new Error(`Review the AppImage launcher before using app-builder-lib ${version}; expected ${SUPPORTED_VERSION}.`);
  }
  if (source.includes(MARKER)) {
    if (source.includes('NO_SANDBOX=(--no-sandbox)')) throw new Error('AppImage sandbox fallback unexpectedly remains.');
    return source;
  }
  const start = source.indexOf('HAVE_NO_SANDBOX=0\n');
  const end = source.indexOf('\natexit()\n', start);
  if (start < 0 || end <= start || source.indexOf('HAVE_NO_SANDBOX=0\n', start + 1) !== -1) {
    throw new Error('AppImage launcher template changed; review it before packaging.');
  }
  const fallback = source.slice(start, end);
  if (!fallback.includes('! unshare -Ur true') || !fallback.includes('NO_SANDBOX=(--no-sandbox)')) {
    throw new Error('Unrecognized AppImage sandbox fallback; review it before packaging.');
  }
  return source.slice(0, start) + `${MARKER}\nNO_SANDBOX=()\n` + source.slice(end);
}

function prepare() {
  const manifest = require.resolve('app-builder-lib/package.json');
  const version = JSON.parse(fs.readFileSync(manifest, 'utf8')).version;
  const target = path.join(path.dirname(manifest), 'out/targets/appimage/appImageUtil.js');
  const original = fs.readFileSync(target, 'utf8');
  const updated = patchSource(original, version);
  if (updated !== original) fs.writeFileSync(target, updated);
  console.log('ACCC Telemetry AppImage launcher preserves the Chromium sandbox.');
}

if (require.main === module) prepare();
module.exports = { patchSource, prepare, MARKER };
