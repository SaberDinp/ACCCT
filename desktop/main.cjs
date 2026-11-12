const { app, BrowserWindow, Menu, dialog, shell, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { BackendProcess, backendCommand } = require('./backend-process.cjs');
const { loadSettings } = require('./settings.cjs');

app.setName('ACCC Telemetry');
const customUserData = process.env.ACCC_DESKTOP_USER_DATA || process.env.APEX_DESKTOP_USER_DATA;
if (customUserData) app.setPath('userData', path.resolve(customUserData));
if (process.platform === 'win32') app.setAppUserModelId('com.accctelemetry.desktop');
let window, backend, origin, settings, settingsPath, log, quitting = false, startupComplete = false;
const projectRoot = path.resolve(__dirname, '..');
const trusted = url => { try { return Boolean(origin) && new URL(url).origin === origin; } catch { return false; } };
const persist = () => fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2));
const dataRoot = () => path.resolve(process.env.ACCC_DATA_ROOT || process.env.APEX_DATA_ROOT || settings.library || path.join(app.getPath('userData'), 'library'));

function external(url) {
  try { if (['https:', 'http:'].includes(new URL(url).protocol)) shell.openExternal(url); } catch { /* malformed URL */ }
}

async function chooseLibrary() {
  const choice = await dialog.showOpenDialog(window, {
    title: 'Open an existing ACCC Telemetry library',
    message: 'Choose the folder containing data/telemetry.sqlite3 (for your current library, choose accTelemetry).',
    properties: ['openDirectory']
  });
  if (choice.canceled) return;
  const selected = choice.filePaths[0];
  if (!fs.existsSync(path.join(selected, 'data', 'telemetry.sqlite3'))) {
    await dialog.showMessageBox(window, { type: 'info', message: 'No telemetry library found',
      detail: 'Choose the parent folder containing data/telemetry.sqlite3.' });
    return;
  }
  settings.library = selected;
  persist();
  app.relaunch();
  app.quit();
}

function createMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    { label: 'File', submenu: [
      { label: 'Open Existing Library…', click: chooseLibrary },
      { label: 'Show Library Folder', click: () => shell.openPath(dataRoot()) },
      { label: 'Use Default Library', click: () => { delete settings.library; persist(); app.relaunch(); app.quit(); } },
      { type: 'separator' }, { role: 'quit' }
    ] },
    { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' },
    { label: 'Help', submenu: [
      { label: 'Rice Racing · Credits & Support', click: () => {
        if (window && origin) window.webContents.executeJavaScript("document.querySelector('[data-view=\"credits\"]')?.click()");
      } },
      { label: 'Show Logs', click: () => shell.openPath(app.getPath('logs')) },
      { label: 'About ACCC Telemetry', click: () => dialog.showMessageBox(window, {
        message: `ACCC Telemetry ${app.getVersion()}`, detail: 'By Rice Racing, Rice University’s Formula SAE team.\n\nAnalyze recorded driving inputs and compare your laps with a reference. Your recordings and analysis stay on your computer.\n\nVisit Help → Rice Racing · Credits & Support to meet the team.'
      }) }
    ] }
  ]));
}

function configureSession() {
  const ses = session.defaultSession;
  // Recorded-video analysis needs no camera, microphone, or screen capture.
  ses.setPermissionCheckHandler((contents, permission, requestingOrigin) =>
    contents === window?.webContents && trusted(requestingOrigin) && ['fullscreen', 'clipboard-sanitized-write'].includes(permission));
  ses.setPermissionRequestHandler((contents, permission, callback, details) => {
    callback(contents === window?.webContents && trusted(details.requestingUrl || contents.getURL()) && ['fullscreen', 'clipboard-sanitized-write'].includes(permission));
  });
  ses.on('will-download', (_event, item) => {
    item.setSaveDialogOptions({ title: 'Save ACCC Telemetry export', defaultPath: path.join(app.getPath('downloads'), item.getFilename()) });
  });
}

async function start() {
  fs.mkdirSync(app.getPath('userData'), { recursive: true });
  app.setAppLogsPath();
  fs.mkdirSync(app.getPath('logs'), { recursive: true });
  settingsPath = path.join(app.getPath('userData'), 'desktop-settings.json');
  settings = loadSettings(app.getPath('userData'), app.getPath('appData'), !customUserData && !process.env.ACCC_DATA_ROOT && !process.env.APEX_DATA_ROOT);
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) settings = {};
  if (!Number.isInteger(settings.port) || settings.port < 1 || settings.port > 65535) delete settings.port;
  if (typeof settings.library !== 'string') delete settings.library;
  // A small rotating log keeps startup failures discoverable without a terminal.
  const logPath = path.join(app.getPath('logs'), 'desktop.log');
  if (fs.existsSync(logPath) && fs.statSync(logPath).size > 5 * 1024 * 1024) fs.renameSync(logPath, `${logPath}.previous`);
  log = fs.createWriteStream(logPath, { flags: 'a' });
  log.on('error', error => console.error('Could not write desktop log:', error.message));
  log.write(`\nACCC ${app.getVersion()} starting ${new Date().toISOString()}\n`);
  const bounds = settings.bounds || {};
  const size = (value, fallback) => Number.isFinite(value) && value >= 600 && value <= 10000 ? Math.round(value) : fallback;
  window = new BrowserWindow({ width: size(bounds.width, 1440), height: size(bounds.height, 960), minWidth: 900, minHeight: 600,
    title: 'ACCC Telemetry', backgroundColor: '#101212', show: false,
    icon: path.join(__dirname, 'icons', 'icon.png'),
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true }
  });
  window.once('ready-to-show', () => window.show());
  window.on('close', () => { settings.bounds = window.getNormalBounds(); persist(); });
  window.on('closed', () => { window = null; app.quit(); });
  window.webContents.setWindowOpenHandler(({ url }) => { external(url); return { action: 'deny' }; });
  window.webContents.on('will-navigate', (event, url) => { if (!trusted(url)) { event.preventDefault(); external(url); } });
  window.webContents.on('will-redirect', (event, url) => { if (!trusted(url)) event.preventDefault(); });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  await window.loadFile(path.join(__dirname, 'loading.html'));
  if (quitting || !window) return;
  configureSession();
  createMenu();
  fs.mkdirSync(dataRoot(), { recursive: true });
  const command = backendCommand({ packaged: app.isPackaged, platform: process.platform,
    resourcesPath: process.resourcesPath, projectRoot, python: process.env.ACCC_PYTHON || process.env.APEX_PYTHON });
  backend = new BackendProcess(command, { dataRoot: dataRoot(), log, runtime: process.execPath, port: settings.port || 0 });
  backend.on('unexpected-exit', () => {
    if (!quitting && startupComplete) {
      dialog.showErrorBox('Analysis engine stopped', `Please reopen ACCC Telemetry. Details are in ${logPath}.`);
      app.quit();
    }
  });
  const ready = await backend.start();
  if (quitting || !window) return;
  origin = ready.url;
  settings.port = ready.port;
  persist();
  await window.loadURL(origin);
  startupComplete = true;
  window.show();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } });
  app.on('activate', () => window?.show());
  app.on('before-quit', event => {
    if (quitting) return;
    event.preventDefault();
    quitting = true;
    Promise.resolve(backend?.stop()).finally(() => { log?.end(); app.quit(); });
  });
  app.whenReady().then(start).catch(error => {
    log?.write(`${error.stack}\n`);
    if (!quitting) dialog.showErrorBox('ACCC Telemetry could not start', `${error.message}\n\nLogs: ${app.getPath('logs')}`);
    app.quit();
  });
}
