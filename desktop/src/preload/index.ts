import { contextBridge, ipcRenderer } from "electron";
import type { ScopeConsumption, ScopeSelection } from "../shared/bridge.js";

// Keep this preload self-contained: sandboxed preload cannot require local modules.
contextBridge.exposeInMainWorld("colinkDesktop", {
  localFiles: {
    select: (request: ScopeSelection) =>
      ipcRenderer.invoke("colink:local-select", request),
    consume: (request: ScopeConsumption) =>
      ipcRenderer.invoke("colink:local-consume", request),
    remove: (id: string) => ipcRenderer.invoke("colink:local-remove", id),
    clear: () => ipcRenderer.invoke("colink:local-clear"),
  },
});

contextBridge.exposeInMainWorld("colinkSetup", {
  state: () => ipcRenderer.invoke("colink:setup"),
  connect: (server: string | null) =>
    ipcRenderer.invoke("colink:connect", server),
});
