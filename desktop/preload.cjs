const { contextBridge, ipcRenderer } = require('electron');

// A synchronous, tiny preference API lets existing UI initialization restore
// selections before its first render. No Node, IPC channel, or path is exposed.
function preference(operation, key, value) {
  const result = ipcRenderer.sendSync('accc:analysis-preferences', operation, key, value);
  if (!result?.ok) throw new Error('Analysis preference could not be accessed.');
  return result.value ?? null;
}
contextBridge.exposeInMainWorld('ACCCDesktopPreferences', Object.freeze({
  getItem: key => preference('get', key),
  setItem: (key, value) => preference('set', key, value)
}));
