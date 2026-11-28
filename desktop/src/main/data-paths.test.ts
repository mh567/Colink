import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import {
  mkdtemp,
  mkdir,
  readFile,
  realpath,
  rm,
  symlink,
  writeFile,
  access,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { createDataPaths } from "./data-paths.js";

test("data paths exist synchronously before Electron can emit ready", async () => {
  const home = await realpath(
    await mkdtemp(join(tmpdir(), "colink-bootstrap-")),
  );
  let result: unknown;
  try {
    result = createDataPaths({}, home);
    assert.equal(result instanceof Promise, false);
    assert.equal(existsSync(join(home, ".colink-v1", "profile")), true);
    assert.equal(existsSync(join(home, ".colink-v1", "server")), true);
  } finally {
    await result;
    await rm(home, { recursive: true, force: true });
  }
});

test("new desktop leaves legacy accounts, sessions and files untouched", async () => {
  const home = await realpath(await mkdtemp(join(tmpdir(), "colink-desktop-")));
  try {
    const legacy = join(home, ".octop");
    await mkdir(legacy);
    await writeFile(join(legacy, "octop.db"), "legacy accounts and sessions");
    await writeFile(join(legacy, "personal.txt"), "keep this file");
    const paths = createDataPaths({ OCTOP_HOME: legacy }, home);
    assert.equal(paths.root, join(home, ".colink-v1"));
    assert.equal(paths.server, join(home, ".colink-v1", "server"));
    assert.equal(
      await readFile(join(legacy, "octop.db"), "utf8"),
      "legacy accounts and sessions",
    );
    assert.equal(
      await readFile(join(legacy, "personal.txt"), "utf8"),
      "keep this file",
    );
  } finally {
    await rm(home, { recursive: true, force: true });
  }
});

test(
  "the real target of an inherited legacy home cannot become the new data root",
  { skip: process.platform === "win32" },
  async () => {
    const home = await realpath(
      await mkdtemp(join(tmpdir(), "colink-legacy-")),
    );
    try {
      const legacy = join(home, "legacy-real");
      const link = join(home, "legacy-link");
      await mkdir(legacy);
      await symlink(legacy, link);
      assert.throws(
        () =>
          createDataPaths(
            { OCTOP_HOME: link, COLINK_DESKTOP_HOME: legacy },
            home,
          ),
        /LEGACY_DATA_DIRECTORY/,
      );
      await assert.rejects(access(join(legacy, "profile")), { code: "ENOENT" });
    } finally {
      await rm(home, { recursive: true, force: true });
    }
  },
);

test(
  "a redirected data directory cannot create files in legacy data",
  { skip: process.platform === "win32" },
  async () => {
    const home = await realpath(
      await mkdtemp(join(tmpdir(), "colink-desktop-")),
    );
    try {
      const legacy = join(home, ".octop");
      await mkdir(legacy);
      await symlink(legacy, join(home, "redirect"));
      assert.throws(() =>
        createDataPaths(
          { COLINK_DESKTOP_HOME: join(home, "redirect", "new") },
          home,
        ),
      );
      await assert.rejects(access(join(legacy, "new")), { code: "ENOENT" });
    } finally {
      await rm(home, { recursive: true, force: true });
    }
  },
);
