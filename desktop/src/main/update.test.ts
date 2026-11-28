import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { checkForUpdate } from "./update/update-coordinator.js";
import { downloadInstaller } from "./update/download.js";
import {
  handOffInstaller,
  installUpdate,
  launchDetachedInstaller,
  type InstallHooks,
} from "./update/installer.js";
import { desktopLocale, desktopUpdateMessage } from "../shared/locales.js";

const source = "https://updates.example.test/colink/releases.json";
const release = (tag = "v1.0.4") => ({
  success: true,
  data: {
    client: {
      stable: {
        tag,
        assets: {
          mac_arm: {
            name: "Colink-desktop-darwin-arm64-1.0.4.dmg",
            ext: "dmg",
            url: "./Colink-desktop-darwin-arm64-1.0.4.dmg",
          },
          win_x64: {
            name: "Colink-desktop-windows-amd64-1.0.4.exe",
            ext: "exe",
            url: "./Colink-desktop-windows-amd64-1.0.4.exe",
          },
          linux_arm: {
            name: "Colink-desktop-linux-arm64-1.0.4.deb",
            ext: "deb",
            url: "./Colink-desktop-linux-arm64-1.0.4.deb",
          },
        },
      },
    },
  },
});

test("an enterprise manifest selects this platform's newer installer and never falls back to a public source", async () => {
  const calls: string[] = [];
  const fetchManifest: typeof fetch = async (input, options) => {
    calls.push(String(input));
    assert.equal(options?.redirect, "error");
    assert.equal(options?.credentials, "omit");
    return Response.json(release());
  };
  await assert.rejects(
    checkForUpdate(undefined, "1.0.3-beta.3", "darwin", "arm64", fetchManifest),
    /UPDATE_NOT_CONFIGURED/,
  );
  assert.deepEqual(calls, []);
  const selected = await checkForUpdate(
    source,
    "1.0.3-beta.3",
    "darwin",
    "arm64",
    fetchManifest,
  );
  assert.deepEqual(selected, {
    version: "1.0.4",
    kind: "dmg",
    filename: "Colink-desktop-darwin-arm64-1.0.4.dmg",
    url: "https://updates.example.test/colink/Colink-desktop-darwin-arm64-1.0.4.dmg",
  });
  assert.deepEqual(calls, [source]);
});

test("the installer is streamed to an isolated temporary directory and incomplete downloads are removed", async () => {
  const root = await mkdtemp(join(tmpdir(), "colink-update-test-"));
  const selected = (await checkForUpdate(
    source,
    "1.0.3",
    "darwin",
    "arm64",
    async () => Response.json(release()),
  ))!;
  try {
    const downloaded = await downloadInstaller(
      selected,
      root,
      async (_url, options) => {
        assert.equal(options?.redirect, "error");
        assert.equal(options?.credentials, "omit");
        return new Response("installer fixture", {
          headers: { "content-length": "17" },
        });
      },
    );
    assert.equal(await readFile(downloaded.path, "utf8"), "installer fixture");
    await downloaded.dispose();
    await assert.rejects(
      downloadInstaller(
        selected,
        root,
        async () =>
          new Response("partial", { headers: { "content-length": "99" } }),
      ),
      /UPDATE_DOWNLOAD_FAILED/,
    );
    assert.deepEqual(await readdir(root), []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("cancelling an update leaves the service running and never downloads or installs", async () => {
  const selected = (await checkForUpdate(
    source,
    "1.0.3",
    "darwin",
    "arm64",
    async () => Response.json(release()),
  ))!;
  const forbidden = async (): Promise<never> => {
    throw new Error("must not run after cancellation");
  };
  assert.equal(
    await installUpdate(selected, {
      confirm: async () => false,
      download: forbidden,
      stop: forbidden,
      handOff: forbidden,
      restore: forbidden,
    }),
    "cancelled",
  );
});

test("update checks reject insecure sources, credentials, foreign assets and redirects", async () => {
  const manifest = async () => Response.json(release());
  for (const address of [
    "http://updates.example.test/releases.json",
    "https://user:secret@updates.example.test/releases.json",
    "file:///tmp/releases.json",
  ]) {
    await assert.rejects(
      checkForUpdate(address, "1.0.3", "darwin", "arm64", manifest),
      /UPDATE_SOURCE_INVALID/,
    );
  }
  for (const address of [
    "https://other.example.test/installer.dmg",
    "https://user:secret@updates.example.test/installer.dmg",
    "file:///tmp/installer.dmg",
  ]) {
    const body = release();
    body.data.client.stable.assets.mac_arm.url = address;
    await assert.rejects(
      checkForUpdate(source, "1.0.3", "darwin", "arm64", async () =>
        Response.json(body),
      ),
      /UPDATE_SOURCE_INVALID/,
    );
  }
  await assert.rejects(
    checkForUpdate(
      source,
      "1.0.3",
      "darwin",
      "arm64",
      async (_url, options) => {
        assert.equal(options?.redirect, "error");
        throw new TypeError("redirect refused");
      },
    ),
    /UPDATE_CHECK_FAILED/,
  );
});

test("a failed or incomplete manifest is not reported as already current", async () => {
  for (const response of [
    Response.json({ success: false }),
    Response.json({ success: true, data: { client: {} } }),
    new Response("not JSON"),
    new Response(" ".repeat(65537)),
  ]) {
    await assert.rejects(
      checkForUpdate(source, "1.0.3", "darwin", "arm64", async () => response),
      /UPDATE_CHECK_FAILED/,
    );
  }
  await assert.rejects(
    checkForUpdate(source, "1.0.3", "linux", "x64", async () =>
      Response.json(release()),
    ),
    /UPDATE_UNSUPPORTED/,
  );
  const wrong = release();
  wrong.data.client.stable.assets.mac_arm.name = "../unrelated.exe";
  await assert.rejects(
    checkForUpdate(source, "1.0.3", "darwin", "arm64", async () =>
      Response.json(wrong),
    ),
    /UPDATE_UNSUPPORTED/,
  );
});

test("release selection preserves stable ordering and refuses a downgrade from a newer beta", async () => {
  assert.equal(
    await checkForUpdate(source, "1.0.5-beta.1", "darwin", "arm64", async () =>
      Response.json(release()),
    ),
    null,
  );
  assert.equal(
    await checkForUpdate(source, "1.0.4", "darwin", "arm64", async () =>
      Response.json(release()),
    ),
    null,
  );
  assert.equal(
    (
      await checkForUpdate(
        source,
        "1.0.4-beta.9",
        "darwin",
        "arm64",
        async () => Response.json(release()),
      )
    )?.version,
    "1.0.4",
  );
});

test("all six packaged platform variants select their own supported installer", async () => {
  const body = release();
  const assets = body.data.client.stable.assets as Record<
    string,
    { name: string; ext: string; url: string }
  >;
  const platforms = [
    ["darwin", "arm64", "mac_arm", "darwin-arm64", "dmg"],
    ["darwin", "x64", "mac_intel", "darwin-amd64", "dmg"],
    ["win32", "x64", "win_x64", "windows-amd64", "exe"],
    ["win32", "arm64", "win_arm", "windows-arm64", "exe"],
    ["linux", "x64", "linux_x64", "linux-amd64", "deb"],
    ["linux", "arm64", "linux_arm", "linux-arm64", "deb"],
  ] as const;
  for (const [platform, arch, key, suffix, kind] of platforms) {
    const name = `Colink-desktop-${suffix}-1.0.4.${kind}`;
    assets[key] = { name, ext: kind, url: `./${name}` };
    const selected = await checkForUpdate(
      source,
      "1.0.3",
      platform,
      arch,
      async () => Response.json(body),
    );
    assert.equal(selected?.filename, name);
    assert.equal(selected?.kind, kind);
  }
});

function installFixture(overrides: Partial<InstallHooks> = {}) {
  const state = { running: true, installed: false, disposed: false };
  const hooks: InstallHooks = {
    confirm: async () => true,
    download: async () => ({
      path: "/isolated/fixture.dmg",
      dispose: async () => {
        state.disposed = true;
      },
    }),
    stop: async () => {
      state.running = false;
      return true;
    },
    handOff: async () => {
      state.installed = true;
    },
    restore: async () => {
      state.running = true;
    },
    ...overrides,
  };
  return { state, hooks };
}

const selectedFixture = {
  version: "1.0.4",
  kind: "dmg" as const,
  filename: "Colink-desktop-darwin-arm64-1.0.4.dmg",
  url: "https://updates.example.test/colink/Colink-desktop-darwin-arm64-1.0.4.dmg",
};

test("a download failure leaves the original service running without starting an installer", async () => {
  const { state, hooks } = installFixture({
    download: async () => {
      throw new Error("download interrupted");
    },
  });
  await assert.rejects(
    installUpdate(selectedFixture, hooks),
    /UPDATE_DOWNLOAD_FAILED/,
  );
  assert.deepEqual(state, { running: true, installed: false, disposed: false });
});

test("a failed installer hand-off restores the original service and removes the staged package", async () => {
  const { state, hooks } = installFixture({
    handOff: async () => {
      throw new Error("no OS installer");
    },
  });
  await assert.rejects(
    installUpdate(selectedFixture, hooks),
    /UPDATE_INSTALL_FAILED/,
  );
  assert.deepEqual(state, { running: true, installed: false, disposed: true });
});

test("a sidecar that remains alive cancels installation and restores the connection", async () => {
  const { state, hooks } = installFixture();
  hooks.stop = async () => {
    state.running = false;
    return false;
  };
  await assert.rejects(
    installUpdate(selectedFixture, hooks),
    /UPDATE_STOP_FAILED/,
  );
  assert.deepEqual(state, { running: true, installed: false, disposed: true });
});

test("a failed recovery is reported instead of claiming the service was restored", async () => {
  const { state, hooks } = installFixture({
    handOff: async () => {
      throw new Error("no OS installer");
    },
    restore: async () => {
      throw new Error("server unavailable");
    },
  });
  await assert.rejects(
    installUpdate(selectedFixture, hooks),
    /UPDATE_RECOVERY_FAILED/,
  );
  assert.deepEqual(state, { running: false, installed: false, disposed: true });
});

test("successful hand-off leaves the stopped service and package to the OS installer", async () => {
  const { state, hooks } = installFixture();
  assert.equal(await installUpdate(selectedFixture, hooks), "opened");
  assert.deepEqual(state, { running: false, installed: true, disposed: false });
});

test("platform hand-off uses OS package handlers and reports a missing handler", async () => {
  const opened: string[] = [];
  const launched: string[] = [];
  const hooks = {
    openPath: async (path: string) => {
      opened.push(path);
      return "";
    },
    launch: async (path: string) => {
      launched.push(path);
    },
  };
  await handOffInstaller("dmg", "/fixture/mac.dmg", "darwin", hooks);
  await handOffInstaller("deb", "/fixture/linux.deb", "linux", hooks);
  await handOffInstaller("exe", "C:\\fixture\\windows.exe", "win32", hooks);
  assert.deepEqual(opened, ["/fixture/mac.dmg", "/fixture/linux.deb"]);
  assert.deepEqual(launched, ["C:\\fixture\\windows.exe"]);
  await assert.rejects(
    handOffInstaller("deb", "/fixture/linux.deb", "linux", {
      ...hooks,
      openPath: async () => "handler missing",
    }),
    /UPDATE_INSTALL_FAILED/,
  );
  await assert.rejects(
    handOffInstaller("exe", "/fixture/wrong.exe", "linux", hooks),
    /UPDATE_UNSUPPORTED/,
  );
  const root = await mkdtemp(join(tmpdir(), "colink-missing-installer-"));
  try {
    await assert.rejects(launchDetachedInstaller(join(root, "missing.exe")), {
      code: "ENOENT",
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("update errors use Chinese or English messages and never expose raw error details", () => {
  for (const code of [
    "UPDATE_NOT_CONFIGURED",
    "UPDATE_SOURCE_INVALID",
    "UPDATE_CHECK_FAILED",
    "UPDATE_UNSUPPORTED",
    "UPDATE_DOWNLOAD_FAILED",
    "UPDATE_STOP_FAILED",
    "UPDATE_INSTALL_FAILED",
    "UPDATE_RECOVERY_FAILED",
  ]) {
    assert.match(desktopUpdateMessage(code, "zh-CN"), /[\u4e00-\u9fff]/);
    assert.match(desktopUpdateMessage(code, "en"), /^[A-Za-z]/);
    assert.notEqual(
      desktopUpdateMessage(code, "zh"),
      desktopUpdateMessage(code, "en"),
    );
  }
  assert.equal(
    desktopUpdateMessage("secret-token-in-network-error", "zh"),
    desktopLocale("zh").UPDATE_CHECK_FAILED,
  );
});
