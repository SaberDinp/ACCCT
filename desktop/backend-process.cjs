const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { EventEmitter } = require('node:events');
const path = require('node:path');

function backendCommand({ packaged, platform, resourcesPath, projectRoot, python }) {
  if (!packaged) return [python || path.join(projectRoot, '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'),
    path.join(projectRoot, 'desktop', 'backend.py')];
  if (platform === 'win32') return [path.join(resourcesPath, 'backend', 'python.exe'),
    path.join(resourcesPath, 'backend', 'app', 'desktop', 'backend.py')];
  if (platform === 'linux') return [path.join(resourcesPath, 'backend', 'python', 'bin', 'python3'),
    path.join(resourcesPath, 'backend', 'app', 'desktop', 'backend.py')];
  return [path.join(resourcesPath, 'backend', 'accc-backend')];
}

class BackendProcess extends EventEmitter {
  constructor(command, { dataRoot, log, runtime, host = '127.0.0.1', port = 0, shutdownTimeout = 35000 }) {
    super();
    this.command = command;
    this.options = { dataRoot, log, runtime, host, port, shutdownTimeout };
    this.stopping = false;
  }

  start(timeout = 120000) {
    if (this.started || this.stopping) return Promise.reject(new Error('The analysis engine has already been started or stopped.'));
    this.started = true;
    const { dataRoot, log, runtime, host, port } = this.options;
    const [executable, ...args] = this.command;
    const env = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONNOUSERSITE: '1',
      ELECTRON_RUN_AS_NODE: '1', ACCC_JS_RUNTIME: `node:${runtime}` };
    // Keep a developer's Python configuration out of a packaged runtime.
    delete env.PYTHONHOME;
    delete env.PYTHONPATH;
    this.child = spawn(executable, [...args, '--data-root', dataRoot, '--host', host, '--port', String(port)], {
      cwd: dataRoot, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
      detached: process.platform !== 'win32'
    });
    this.child.stdin.on('error', () => {});
    this.child.stderr.on('data', chunk => log.write(chunk));
    this.exited = new Promise(resolve => {
      const finish = (code, signal, error) => {
        if (this.dead) return;
        this.dead = true;
        resolve();
        if (!this.stopping) this.emit('unexpected-exit', { code, signal, error });
      };
      this.child.once('exit', finish);
      // Spawn failures do not emit 'exit', so they must settle ownership too.
      this.child.once('error', error => { if (!this.child.pid) finish(null, null, error); });
    });
    return new Promise((resolve, reject) => {
      let settled = false;
      const fail = error => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      };
      const timer = setTimeout(() => {
        fail(new Error(`The analysis engine did not start within ${timeout / 1000} seconds.`));
        this.stop().catch(error => log.write(`Stopping timed-out engine: ${error.message}\n`));
      }, timeout);
      this.child.once('error', fail);
      this.child.once('exit', code => fail(new Error(`The analysis engine stopped during startup (code ${code}).`)));
      const lines = createInterface({ input: this.child.stdout });
      lines.on('line', line => {
        log.write(`${line}\n`);
        let record;
        try { record = JSON.parse(line); } catch { return; }
        if (!settled && record && record.type === 'ready' && Number.isInteger(record.port) && record.port > 0 && record.port <= 65535 && record.pid === this.child.pid) {
          settled = true;
          clearTimeout(timer);
          resolve({ ...record, url: `http://127.0.0.1:${record.port}` });
        }
      });
    });
  }

  stop() {
    this.stopPromise ||= this._stop();
    return this.stopPromise;
  }

  async _stop() {
    this.stopping = true;
    if (!this.child || this.dead || !this.child.pid) return;
    this.child.stdin.end('shutdown\n');
    let timer;
    const exited = await Promise.race([this.exited.then(() => true),
      new Promise(resolve => { timer = setTimeout(() => resolve(false), this.options.shutdownTimeout); })]);
    clearTimeout(timer);
    if (!exited) {
      if (process.platform === 'win32') {
        const killer = spawn('taskkill', ['/PID', String(this.child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
        await new Promise(resolve => { killer.once('exit', resolve); killer.once('error', resolve); });
        if (!this.dead) this.child.kill('SIGKILL');
      } else {
        try { process.kill(-this.child.pid, 'SIGKILL'); } catch { /* already stopped */ }
      }
      try {
        await Promise.race([this.exited,
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('The analysis engine could not be stopped.')), 5000); })]);
      } finally { clearTimeout(timer); }
    }
  }
}

module.exports = { BackendProcess, backendCommand };
