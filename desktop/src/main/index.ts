import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Menu,
  net,
  shell,
  type IpcMainInvokeEvent,
  type MenuItemConstructorOptions,
} from "electron";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import {
  CHANNELS,
  type ScopeConsumption,
  type ScopeSelection,
} from "../shared/bridge.js";
import { desktopLocale, desktopUpdateMessage } from "../shared/locales.js";
import { createDataPaths, type DataPaths } from "./data-paths.js";
import { readConnection, saveConnection } from "./connection-store.js";
import { LocalFiles } from "./local-files.js";
import {
  buildSidecarEnv,
  ensureWizardPassword,
  portableLaunch,
  reserveRandomPort,
  SidecarSupervisor,
} from "./sidecar.js";
import { normalizeServerURL, trustedPage, verifySession } from "./trust.js";
import { checkForUpdate } from "./update/update-coordinator.js";
import { downloadInstaller } from "./update/download.js";
import {
  handOffInstaller,
  installUpdate,
  launchDetachedInstaller,
} from "./update/installer.js";

app.setName("Colink");
app.enableSandbox();

let window: BrowserWindow | undefined;
let paths: DataPaths;
let server: string | undefined;
let savedServer: string | null = null;
let sidecar: SidecarSupervisor | undefined;
let connecting = false;
let quitting = false;
let updating = false;
let updateAbort: AbortController | undefined;
let setupError = "";
const setupPage = pathToFileURL(
  join(__dirname, "../../renderer/connect.html"),
).href;
const local = new LocalFiles(async (session) => {
  if (!server) throw new Error("LOCAL_SCOPE_AUTH_FAILED");
  return verifySession(server, session, net.fetch as typeof fetch);
});
const copy = (): ReturnType<typeof desktopLocale> =>
  desktopLocale(app.getLocale());

function sender(event: IpcMainInvokeEvent, setup = false): void {
  if (
    !window ||
    event.sender !== window.webContents ||
    event.senderFrame !== window.webContents.mainFrame
  )
    throw new Error("LOCAL_SCOPE_AUTH_FAILED");
  const url = event.senderFrame.url;
  if (setup ? url !== setupPage : !server || !trustedPage(url, server))
    throw new Error("LOCAL_SCOPE_AUTH_FAILED");
}

async function showSetup(error = ""): Promise<void> {
  local.clear();
  server = undefined;
  setupError = error;
  if (window && !window.isDestroyed()) await window.loadURL(setupPage);
}

async function stopSidecar(): Promise<{ stopped: true }> {
  const child = sidecar;
  if (!child) return { stopped: true };
  const result = await child.stop();
  if (sidecar === child) sidecar = undefined;
  return result;
}

async function updateDesktop(): Promise<void> {
  if (updating || connecting || !window || window.isDestroyed()) return;
  updating = true;
  updateAbort = new AbortController();
  const controller = updateAbort;
  const item = Menu.getApplicationMenu()?.getMenuItemById("desktop-update");
  if (item) item.enabled = false;
  try {
    const selected = await checkForUpdate(
      process.env.COLINK_UPDATE_MANIFEST_URL,
      app.getVersion(),
      process.platform,
      process.arch,
      net.fetch as typeof fetch,
    );
    if (quitting || controller.signal.aborted) return;
    if (!selected) {
      await dialog.showMessageBox(window, {
        type: "info",
        title: copy().updateTitle,
        message: copy().updateCurrent,
      });
      return;
    }
    const reconnect = sidecar ? null : server;
    const outcome = await installUpdate(selected, {
      confirm: async (release) => {
        const strings = copy();
        const result = await dialog.showMessageBox(window!, {
          type: "question",
          title: strings.updateTitle,
          message: strings.updateConfirm.replace("{version}", release.version),
          buttons: [strings.cancel, strings.updateInstall],
          defaultId: 0,
          cancelId: 0,
        });
        return result.response === 1 && !quitting && !controller.signal.aborted;
      },
      download: (release) =>
        downloadInstaller(
          release,
          app.getPath("temp"),
          net.fetch as typeof fetch,
          {
            signal: controller.signal,
            onProgress: (received, total) => {
              if (window && !window.isDestroyed())
                window.setProgressBar(total > 0 ? received / total : 2);
            },
          },
        ),
      stop: async () => {
        if (quitting || controller.signal.aborted)
          throw new Error("UPDATE_STOP_FAILED");
        await showSetup(copy().updateInstalling);
        return (await stopSidecar()).stopped;
      },
      handOff: (kind, path) =>
        handOffInstaller(kind, path, process.platform, {
          openPath: shell.openPath,
          launch: launchDetachedInstaller,
        }),
      restore: async () => {
        if (quitting) return;
        if (reconnect === undefined) await showSetup();
        else {
          await connect(reconnect, false);
          if (!server) throw new Error("UPDATE_RECOVERY_FAILED");
        }
      },
    });
    if (outcome === "opened") app.quit();
  } catch (error) {
    if (!quitting && window && !window.isDestroyed())
      await dialog.showMessageBox(window, {
        type: "error",
        title: copy().updateTitle,
        message: desktopUpdateMessage(
          error instanceof Error ? error.message : "",
          app.getLocale(),
        ),
      });
  } finally {
    updating = false;
    updateAbort = undefined;
    if (item) item.enabled = true;
    if (window && !window.isDestroyed()) window.setProgressBar(-1);
  }
}

async function connect(value: string | null, persist: boolean): Promise<void> {
  if (connecting) throw new Error("CONNECTING");
  connecting = true;
  local.clear();
  try {
    await stopSidecar();
    let target: string;
    if (value !== null) {
      if (typeof value !== "string" || value.length > 2048)
        throw new Error("INVALID_SERVER_URL");
      target = normalizeServerURL(value);
      const response = await net.fetch(new URL("api/health", target).href, {
        redirect: "error",
        signal: AbortSignal.timeout(10_000),
      });
      const body = (await response.json()) as { ok?: boolean };
      if (!response.ok || body.ok !== true)
        throw new Error("SERVER_UNAVAILABLE");
    } else {
      const platform = `${
        process.platform === "win32" ? "windows" : process.platform
      }-${process.arch === "x64" ? "amd64" : process.arch}`;
      const portable = app.isPackaged
        ? join(process.resourcesPath, "portable")
        : join(app.getAppPath(), "../portable/release", `Octop-${platform}`);
      const launch = await portableLaunch(portable);
      const port = await reserveRandomPort();
      await ensureWizardPassword(paths.server);
      sidecar = new SidecarSupervisor(
        {
          ...launch,
          port,
          env: buildSidecarEnv(process.env, {
            server: paths.server,
            portable,
            port,
          }),
        },
        () => {
          local.clear();
          if (!quitting && !connecting)
            void stopSidecar()
              .then(() => showSetup(copy().failed))
              .catch(() => showSetup(copy().quitFailed));
        },
      );
      sidecar.start();
      await sidecar.waitUntilReady();
      target = `http://127.0.0.1:${port}/`;
    }
    if (quitting) {
      await stopSidecar();
      return;
    }
    if (persist) {
      await saveConnection(paths.root, value === null ? null : target);
      savedServer = value === null ? null : target;
    }
    server = target;
    await window!.loadURL(target);
  } catch (error) {
    await stopSidecar();
    await showSetup(
      error instanceof Error && error.message === "BUNDLED_RUNTIME_MISSING"
        ? copy().localMissing
        : copy().failed,
    );
  } finally {
    connecting = false;
  }
}

function registerIPC(): void {
  ipcMain.handle(CHANNELS.setup, (event) => {
    sender(event, true);
    return { copy: copy(), server: savedServer ?? "", error: setupError };
  });
  ipcMain.handle(CHANNELS.connect, async (event, value: string | null) => {
    sender(event, true);
    if (updating) throw new Error("UPDATE_IN_PROGRESS");
    await connect(value, true);
  });
  ipcMain.handle(CHANNELS.select, async (event, request: ScopeSelection) => {
    sender(event);
    return local.select(request, async () => {
      const strings = desktopLocale(request.locale);
      const result = await dialog.showOpenDialog(window!, {
        title:
          request.kind === "directory"
            ? strings.selectDirectory
            : strings.selectFiles,
        buttonLabel: strings.select,
        message: strings.localNotice,
        properties:
          request.kind === "directory"
            ? ["openDirectory", "dontAddToRecent"]
            : ["openFile", "multiSelections", "dontAddToRecent"],
        filters:
          request.kind === "files"
            ? [{ name: strings.files, extensions: ["txt", "md", "csv"] }]
            : [],
      });
      return result.canceled ? [] : result.filePaths;
    });
  });
  ipcMain.handle(CHANNELS.consume, (event, request: ScopeConsumption) => {
    sender(event);
    return local.consume(request);
  });
  ipcMain.handle(CHANNELS.remove, (event, id: string) => {
    sender(event);
    local.remove(id);
  });
  ipcMain.handle(CHANNELS.clear, (event) => {
    sender(event);
    local.clear();
  });
}

function createWindow(): void {
  window = new BrowserWindow({
    title: "Colink",
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 600,
    show: false,
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      allowRunningInsecureContent: false,
    },
  });
  window.once("ready-to-show", () => window?.show());
  window.on("close", () => local.clear());
  window.on("closed", () => {
    window = undefined;
    app.quit();
  });
  const contents = window.webContents;
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  contents.on("will-attach-webview", (event) => event.preventDefault());
  const allowed = (url: string): boolean =>
    url === setupPage || Boolean(server && trustedPage(url, server));
  contents.on("will-navigate", (event, url) => {
    local.clear();
    if (!allowed(url)) event.preventDefault();
  });
  contents.on("will-redirect", (event, url) => {
    local.clear();
    if (!allowed(url)) event.preventDefault();
  });
  contents.on("will-frame-navigate", (event) => {
    if (!event.isMainFrame || !allowed(event.url)) event.preventDefault();
  });
  contents.on("did-start-navigation", (event) => {
    if (event.isMainFrame) local.clear();
  });
  contents.on("render-process-gone", () => local.clear());
  contents.session.setPermissionRequestHandler(
    (_contents, _permission, callback) => callback(false),
  );
  contents.session.setPermissionCheckHandler(() => false);
  const strings = copy();
  const menu: MenuItemConstructorOptions[] = [
    ...(process.platform === "darwin" ? [{ role: "appMenu" as const }] : []),
    {
      label: strings.fileMenu,
      submenu: [
        {
          label: strings.serverMenu,
          click: () => {
            if (updating) return;
            void stopSidecar()
              .then(() => showSetup())
              .catch(() => showSetup(copy().failed));
          },
        },
        {
          id: "desktop-update",
          label: strings.updateMenu,
          click: () => {
            void updateDesktop();
          },
        },
        { type: "separator" },
        { role: "quit", label: strings.quit },
      ],
    },
    {
      label: strings.editMenu,
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "selectAll" },
      ],
    },
    {
      label: strings.viewMenu,
      submenu: [
        { role: "reload" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { role: "togglefullscreen" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(menu));
}

async function start(): Promise<void> {
  // Chromium fixes cache access when ready fires; these paths must be set in the first tick.
  paths = createDataPaths(process.env);
  app.setPath("userData", paths.profile);
  app.setPath("sessionData", paths.profile);
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }
  app.on("second-instance", () => {
    window?.show();
    window?.focus();
  });
  await app.whenReady();
  createWindow();
  registerIPC();
  await showSetup();
  try {
    savedServer = (await readConnection(paths.root)) ?? null;
  } catch {
    await showSetup(copy().failed);
    return;
  }
  const configured = process.env.COLINK_SERVER_URL;
  await connect(configured || savedServer, false);
}

app.on("before-quit", (event) => {
  local.clear();
  updateAbort?.abort();
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  void stopSidecar()
    .catch(() => dialog.showErrorBox("Colink", copy().quitFailed))
    .finally(() => app.quit());
});
void start().catch(async () => {
  await app.whenReady();
  dialog.showErrorBox("Colink", copy().failed);
  app.quit();
});
