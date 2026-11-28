// Installer hand-off adapted from TencentCloud/Octop v1.0.3b7 (MIT).
import { spawn } from "node:child_process";
import type { DownloadedInstaller } from "./download.js";
import type { InstallerKind, SelectedRelease } from "./update-coordinator.js";

export interface InstallHooks {
  confirm(selected: SelectedRelease): Promise<boolean>;
  download(selected: SelectedRelease): Promise<DownloadedInstaller>;
  stop(): Promise<boolean>;
  handOff(kind: InstallerKind, path: string): Promise<void>;
  restore(): Promise<void>;
}

export async function installUpdate(
  selected: SelectedRelease,
  hooks: InstallHooks,
): Promise<"cancelled" | "opened"> {
  if (!(await hooks.confirm(selected))) return "cancelled";
  let downloaded: DownloadedInstaller;
  try {
    downloaded = await hooks.download(selected);
  } catch {
    throw new Error("UPDATE_DOWNLOAD_FAILED");
  }
  let failure: string | undefined;
  try {
    if (!(await hooks.stop())) throw new Error("UPDATE_STOP_FAILED");
  } catch {
    failure = "UPDATE_STOP_FAILED";
  }
  if (!failure) {
    try {
      await hooks.handOff(selected.kind, downloaded.path);
      return "opened";
    } catch {
      failure = "UPDATE_INSTALL_FAILED";
    }
  }
  try {
    await hooks.restore();
  } catch {
    failure = "UPDATE_RECOVERY_FAILED";
  } finally {
    await downloaded.dispose();
  }
  throw new Error(failure);
}

export async function handOffInstaller(
  kind: InstallerKind,
  path: string,
  platform: NodeJS.Platform,
  hooks: {
    openPath(path: string): Promise<string>;
    launch(path: string): Promise<void>;
  },
): Promise<void> {
  if (platform === "win32" && kind === "exe") {
    await hooks.launch(path);
  } else if (
    (platform === "darwin" && kind === "dmg") ||
    (platform === "linux" && kind === "deb")
  ) {
    if (await hooks.openPath(path)) throw new Error("UPDATE_INSTALL_FAILED");
  } else {
    throw new Error("UPDATE_UNSUPPORTED");
  }
}

export function launchDetachedInstaller(path: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const installer = spawn(path, [], { detached: true, stdio: "ignore" });
    installer.once("error", reject);
    installer.once("spawn", () => {
      installer.unref();
      resolve();
    });
  });
}
