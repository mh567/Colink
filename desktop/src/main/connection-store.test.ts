import assert from "node:assert/strict";
import {
  mkdtemp,
  readFile,
  realpath,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { readConnection, saveConnection } from "./connection-store.js";

test("the server choice persists without following a configuration symlink", async () => {
  const dir = await realpath(
    await mkdtemp(join(tmpdir(), "colink-connection-")),
  );
  try {
    assert.equal(await readConnection(dir), undefined);
    await saveConnection(dir, "https://colink.example.com/");
    assert.equal(await readConnection(dir), "https://colink.example.com/");
    await saveConnection(dir, null);
    assert.equal(await readConnection(dir), null);
    if (process.platform !== "win32") {
      const legacy = join(dir, "legacy.json");
      await writeFile(legacy, "legacy settings");
      await rm(join(dir, "connection.json"));
      await symlink(legacy, join(dir, "connection.json"));
      await assert.rejects(
        saveConnection(dir, "https://other.example.com/"),
        /UNSAFE_DATA_DIRECTORY/,
      );
      await assert.rejects(readConnection(dir));
      assert.equal(await readFile(legacy, "utf8"), "legacy settings");
    }
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
