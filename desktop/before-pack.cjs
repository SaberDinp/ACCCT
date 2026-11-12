// Fail before making an installer if the bundled runtime targets another OS/CPU.
const fs = require('node:fs');
const path = require('node:path');
const { Arch } = require('builder-util');

module.exports = async context => {
  const target = Arch[context.arch];
  if (context.electronPlatformName === 'darwin') {
    const folder = path.join(__dirname, `build/backend-macos-${target}/accc-backend`);
    const metadata = JSON.parse(fs.readFileSync(path.join(folder, 'build-info.json')));
    const architecture = metadata.architecture === 'x86_64' ? 'x64' : metadata.architecture;
    if (metadata.platform !== 'darwin' || architecture !== target) throw new Error('Rebuild the macOS backend for the selected architecture.');
    const version = text => text.split('.').reduce((total, value, index) => total + Number(value) * (index === 0 ? 100 : 1), 0);
    const supportedFloor = target === 'x64' ? '13.0' : '14.0';
    if (!metadata.minimum_macos || version(metadata.minimum_macos) > version(supportedFloor)) {
      throw new Error(`Backend requires macOS ${metadata.minimum_macos || 'unknown'}; rebuild using a portable Python compatible with macOS ${supportedFloor}.`);
    }
    context.packager.platformSpecificBuildOptions.minimumSystemVersion = supportedFloor;
    fs.accessSync(path.join(folder, 'accc-backend'), fs.constants.X_OK);
  } else if (context.electronPlatformName === 'win32') {
    const folder = path.join(__dirname, 'build/backend-windows');
    const metadata = JSON.parse(fs.readFileSync(path.join(folder, 'bundle-manifest.json')));
    if (metadata.platform !== 'win32' || metadata.architecture !== target) throw new Error('Rebuild the Windows backend for the selected architecture.');
    for (const filename of ['python.exe', 'python314._pth', 'app/desktop/backend.py', 'app/scripts/validate_corpus.py']) {
      fs.accessSync(path.join(folder, filename));
    }
  } else if (context.electronPlatformName === 'linux') {
    const { MARKER } = require('./prepare_linux_packaging.cjs');
    const { generateAppRunScript } = require('app-builder-lib/out/targets/appimage/appImageUtil.js');
    const launcher = generateAppRunScript({ ExecutableName: 'accc-telemetry' });
    if (!launcher.includes(MARKER) || launcher.includes('NO_SANDBOX=(--no-sandbox)')) {
      throw new Error('Run npm run pack:linux (or node desktop/prepare_linux_packaging.cjs) before packaging Linux; the AppImage launcher must preserve the Chromium sandbox.');
    }
    const folder = path.join(__dirname, `build/backend-linux-${target}`);
    const metadata = JSON.parse(fs.readFileSync(path.join(folder, 'bundle-manifest.json')));
    if (metadata.platform !== 'linux' || metadata.architecture !== target) throw new Error('Rebuild the Linux backend for the selected architecture.');
    fs.accessSync(path.join(folder, 'python/bin/python3'), fs.constants.X_OK);
    fs.accessSync(path.join(folder, 'app/scripts/validate_corpus.py'));
  }
};
