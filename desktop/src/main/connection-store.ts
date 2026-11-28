import { randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { normalizeServerURL } from "./trust.js";

async function checkFile(path: string): Promise<void> {
  try {
    const entry = await lstat(path);
    if (!entry.isFile() || entry.isSymbolicLink())
      throw new Error("UNSAFE_DATA_DIRECTORY");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

export async function readConnection(
  root: string,
): Promise<string | null | undefined> {
  const path = join(root, "connection.json");
  await checkFile(path);
  try {
    const handle = await open(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
    try {
      const file = await handle.stat();
      if (!file.isFile() || file.size > 8192)
        throw new Error("INVALID_SERVER_URL");
      const settings = JSON.parse(await handle.readFile("utf8")) as {
        server?: unknown;
      };
      if (settings.server !== null && typeof settings.server !== "string")
        throw new Error("INVALID_SERVER_URL");
      return settings.server === null
        ? null
        : normalizeServerURL(settings.server);
    } finally {
      await handle.close();
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}

export async function saveConnection(
  root: string,
  server: string | null,
): Promise<void> {
  const path = join(root, "connection.json");
  await checkFile(path);
  const temporary = join(root, `.connection-${randomUUID()}.json`);
  try {
    await writeFile(temporary, JSON.stringify({ server }), {
      flag: "wx",
      mode: 0o600,
    });
    // Renaming replaces the directory entry; it cannot follow a late symlink on Windows.
    await rename(temporary, path);
  } finally {
    await rm(temporary, { force: true });
  }
}
