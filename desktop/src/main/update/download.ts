// Streaming download adapted from TencentCloud/Octop v1.0.3b7 (MIT).
import { createWriteStream } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { basename, join } from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import type { SelectedRelease } from "./update-coordinator.js";

export interface DownloadedInstaller {
  path: string;
  dispose(): Promise<void>;
}

export async function downloadInstaller(
  selected: SelectedRelease,
  tempRoot: string,
  fetchImpl: typeof fetch = fetch,
  options: {
    signal?: AbortSignal;
    onProgress?: (received: number, total: number) => void;
  } = {},
): Promise<DownloadedInstaller> {
  if (basename(selected.filename) !== selected.filename)
    throw new Error("UPDATE_DOWNLOAD_FAILED");
  const directory = await mkdtemp(join(tempRoot, "colink-update-"));
  const path = join(directory, selected.filename);
  const dispose = async (): Promise<void> =>
    rm(directory, { recursive: true, force: true });
  const timeout = AbortSignal.timeout(15 * 60_000);
  const signal = options.signal
    ? AbortSignal.any([timeout, options.signal])
    : timeout;
  try {
    const response = await fetchImpl(selected.url, {
      cache: "no-store",
      redirect: "error",
      credentials: "omit",
      signal,
    });
    if (!response.ok || !response.body) throw new Error();
    const total = Number(response.headers.get("content-length")) || 0;
    let received = 0;
    const counter = new Transform({
      transform(chunk: Buffer, _encoding, callback) {
        received += chunk.length;
        options.onProgress?.(received, total);
        callback(null, chunk);
      },
    });
    await pipeline(
      Readable.fromWeb(
        response.body as import("node:stream/web").ReadableStream<Uint8Array>,
      ),
      counter,
      createWriteStream(path, { flags: "wx", mode: 0o600 }),
      { signal },
    );
    if (received === 0 || (total > 0 && received !== total)) throw new Error();
    return { path, dispose };
  } catch {
    await dispose();
    throw new Error("UPDATE_DOWNLOAD_FAILED");
  }
}
