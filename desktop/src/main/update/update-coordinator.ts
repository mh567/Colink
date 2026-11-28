// Release selection adapted from TencentCloud/Octop v1.0.3b7 (MIT).
export type InstallerKind = "dmg" | "exe" | "deb";

export interface SelectedRelease {
  version: string;
  kind: InstallerKind;
  filename: string;
  url: string;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("UPDATE_CHECK_FAILED");
  return value as Record<string, unknown>;
}

function version(value: string): number[] {
  const normalized = value.replace(
    /-(alpha|beta|rc)\./,
    (_, stage: string) => ({ alpha: "a", beta: "b", rc: "rc" })[stage]!,
  );
  const match = /^v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$/.exec(normalized);
  if (!match) throw new Error("UPDATE_CHECK_FAILED");
  return [
    Number(match[1]),
    Number(match[2]),
    Number(match[3]),
    match[4] ? { a: 0, b: 1, rc: 2 }[match[4]]! : 3,
    Number(match[5] ?? 0),
  ];
}

function newer(candidate: string, current: string): boolean {
  const left = version(candidate);
  const right = version(current);
  for (let i = 0; i < left.length; i++) {
    if (left[i] !== right[i]) return left[i] > right[i];
  }
  return false;
}

function updateSource(value: string | undefined): URL {
  if (!value?.trim()) throw new Error("UPDATE_NOT_CONFIGURED");
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.hash)
      throw new Error();
    return url;
  } catch {
    throw new Error("UPDATE_SOURCE_INVALID");
  }
}

async function readManifest(response: Response): Promise<unknown> {
  if (!response.ok || !response.body) throw new Error("UPDATE_CHECK_FAILED");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > 64 * 1024) throw new Error("UPDATE_CHECK_FAILED");
      chunks.push(chunk.value);
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } finally {
    await reader.cancel();
    reader.releaseLock();
  }
}

export async function checkForUpdate(
  source: string | undefined,
  currentVersion: string,
  platform: NodeJS.Platform,
  arch: string,
  fetchImpl: typeof fetch = fetch,
): Promise<SelectedRelease | null> {
  const manifestURL = updateSource(source);
  const systems = {
    darwin: { prefix: "mac", os: "darwin", kind: "dmg" },
    win32: { prefix: "win", os: "windows", kind: "exe" },
    linux: { prefix: "linux", os: "linux", kind: "deb" },
  } as const;
  const system = systems[platform as keyof typeof systems];
  if (!system || !["x64", "arm64"].includes(arch))
    throw new Error("UPDATE_UNSUPPORTED");
  try {
    const response = await fetchImpl(manifestURL.href, {
      redirect: "error",
      cache: "no-store",
      credentials: "omit",
      signal: AbortSignal.timeout(15_000),
    });
    const manifest = record(await readManifest(response));
    if (manifest.success !== true) throw new Error("UPDATE_CHECK_FAILED");
    const stable = record(record(record(manifest.data).client).stable);
    if (typeof stable.tag !== "string") throw new Error("UPDATE_CHECK_FAILED");
    if (!newer(stable.tag, currentVersion)) return null;
    const key = `${system.prefix}_${
      arch === "arm64" ? "arm" : platform === "darwin" ? "intel" : "x64"
    }`;
    const entry = record(stable.assets)[key];
    if (entry === undefined) throw new Error("UPDATE_UNSUPPORTED");
    const asset = record(entry);
    const releaseVersion = stable.tag.replace(/^v/, "");
    const filename = `Colink-desktop-${system.os}-${
      arch === "x64" ? "amd64" : "arm64"
    }-${releaseVersion}.${system.kind}`;
    if (
      asset.name !== filename ||
      asset.ext !== system.kind ||
      typeof asset.url !== "string"
    )
      throw new Error("UPDATE_UNSUPPORTED");
    const url = new URL(asset.url, manifestURL);
    if (
      url.origin !== manifestURL.origin ||
      url.username ||
      url.password ||
      url.hash
    )
      throw new Error("UPDATE_SOURCE_INVALID");
    return {
      version: releaseVersion,
      filename,
      kind: system.kind,
      url: url.href,
    };
  } catch (error) {
    if (
      error instanceof Error &&
      ["UPDATE_UNSUPPORTED", "UPDATE_SOURCE_INVALID"].includes(error.message)
    )
      throw error;
    throw new Error("UPDATE_CHECK_FAILED");
  }
}
