import { randomUUID } from "node:crypto";
import { constants, type Stats } from "node:fs";
import { lstat, open, readdir, realpath } from "node:fs/promises";
import { basename, extname, isAbsolute, join, relative, sep } from "node:path";
import type {
  LocalContext,
  LocalScope,
  ScopeConsumption,
  ScopeSelection,
  ScopeSession,
} from "../shared/bridge.js";
import { desktopLocale } from "../shared/locales.js";

type Scope = LocalScope & { device: number; inode: number };
type File = { path: string; scope: Scope; stat: Stats };
const MAX_FILES = 20;
const MAX_BYTES = 1024 * 1024;
const MAX_CHARACTERS = 60_000;

function fail(code: string): never {
  throw new Error(code);
}

function validateSession(session: ScopeSession): void {
  if (
    !session ||
    [session.accessToken, session.agentId, session.conversationId].some(
      (v) => typeof v !== "string" || !v || v.length > 8192,
    )
  ) {
    fail("LOCAL_SCOPE_INVALID");
  }
  if (
    session.locale !== undefined &&
    session.locale !== "zh" &&
    session.locale !== "en"
  )
    fail("LOCAL_SCOPE_INVALID");
}

function contains(root: string, path: string): boolean {
  const part = relative(root, path);
  return (
    part === "" ||
    (part !== ".." && !part.startsWith(`..${sep}`) && !isAbsolute(part))
  );
}

function sameFile(
  a: Pick<Stats, "dev" | "ino">,
  b: Pick<Stats, "dev" | "ino">,
): boolean {
  return a.dev === b.dev && a.ino === b.ino;
}

export class LocalFiles {
  private scopes = new Map<string, Scope>();
  private generation = 0;
  private binding: string | undefined;

  constructor(
    private readonly verifySession: (session: ScopeSession) => Promise<number>,
  ) {}

  clear(): void {
    this.generation++;
    this.scopes.clear();
    this.binding = undefined;
  }

  remove(id: string): void {
    if (typeof id !== "string") fail("LOCAL_SCOPE_INVALID");
    this.generation++;
    this.scopes.delete(id);
  }

  private active(generation: number): void {
    if (generation !== this.generation) fail("LOCAL_SCOPE_REVOKED");
  }

  private async identity(
    session: ScopeSession,
    generation: number,
  ): Promise<string> {
    validateSession(session);
    let userId: number;
    try {
      userId = await this.verifySession(session);
      if (!Number.isSafeInteger(userId) || userId <= 0)
        throw new Error("invalid-user");
    } catch {
      this.active(generation);
      this.clear();
      fail("LOCAL_SCOPE_AUTH_FAILED");
    }
    this.active(generation);
    const binding = JSON.stringify([
      userId,
      session.agentId,
      session.conversationId,
    ]);
    if (this.binding !== undefined && this.binding !== binding) {
      this.clear();
      fail("LOCAL_SCOPE_IDENTITY_CHANGED");
    }
    // Bind before yielding to a picker so concurrent sessions cannot mix grants.
    this.binding = binding;
    return binding;
  }

  async select(
    request: ScopeSelection,
    pick: () => Promise<string[]>,
  ): Promise<LocalScope[]> {
    if (!request || (request.kind !== "files" && request.kind !== "directory"))
      fail("LOCAL_SCOPE_INVALID");
    const generation = this.generation;
    const binding = await this.identity(request, generation);
    const paths = await pick();
    this.active(generation);
    if (paths.length === 0) return [];
    if (
      paths.length > MAX_FILES ||
      (request.kind === "directory" && paths.length !== 1)
    )
      fail("LOCAL_SCOPE_LIMIT");
    if ((await this.identity(request, generation)) !== binding) {
      this.clear();
      fail("LOCAL_SCOPE_IDENTITY_CHANGED");
    }
    try {
      const selected: Scope[] = [];
      for (const path of paths) {
        const stat = await lstat(path);
        if (
          stat.isSymbolicLink() ||
          !(request.kind === "files" ? stat.isFile() : stat.isDirectory())
        )
          fail("LOCAL_SCOPE_UNSAFE_PATH");
        const canonical = await realpath(path);
        const current = await lstat(canonical);
        if (!sameFile(stat, current)) fail("LOCAL_SCOPE_UNSAFE_PATH");
        selected.push({
          id: randomUUID(),
          name: basename(canonical),
          path: canonical,
          kind: request.kind === "files" ? "file" : "directory",
          device: stat.dev,
          inode: stat.ino,
        });
      }
      this.active(generation);
      for (const scope of selected) this.scopes.set(scope.id, scope);
      return selected.map(
        ({ device: _device, inode: _inode, ...scope }) => scope,
      );
    } catch (error) {
      this.readError(error);
    }
  }

  private async checkScope(scope: Scope): Promise<void> {
    const stat = await lstat(scope.path);
    if (
      stat.isSymbolicLink() ||
      stat.dev !== scope.device ||
      stat.ino !== scope.inode ||
      (await realpath(scope.path)) !== scope.path
    )
      fail("LOCAL_SCOPE_UNSAFE_PATH");
  }

  private async collect(
    scope: Scope,
    path: string,
    files: File[],
    generation: number,
  ): Promise<void> {
    this.active(generation);
    await this.checkScope(scope);
    const stat = await lstat(path);
    if (
      stat.isSymbolicLink() ||
      (await realpath(path)) !== path ||
      !contains(scope.path, path)
    )
      fail("LOCAL_SCOPE_UNSAFE_PATH");
    if (stat.isDirectory()) {
      for (const name of (await readdir(path)).sort())
        await this.collect(scope, join(path, name), files, generation);
    } else if (stat.isFile()) {
      if (![".txt", ".md", ".csv"].includes(extname(path).toLowerCase()))
        fail("LOCAL_SCOPE_UNSUPPORTED_FILE");
      if (stat.size > MAX_BYTES || files.length >= MAX_FILES)
        fail("LOCAL_SCOPE_LIMIT");
      files.push({ path, scope, stat });
    } else fail("LOCAL_SCOPE_UNSAFE_PATH");
  }

  private readError(error: unknown): never {
    if (error instanceof Error && /^LOCAL_SCOPE_[A-Z_]+$/.test(error.message))
      throw error;
    fail("LOCAL_SCOPE_READ_FAILED");
  }

  async consume(request: ScopeConsumption): Promise<LocalContext> {
    if (
      !request ||
      !Array.isArray(request.ids) ||
      request.ids.length === 0 ||
      request.ids.length > MAX_FILES ||
      request.ids.some((id) => typeof id !== "string") ||
      new Set(request.ids).size !== request.ids.length
    )
      fail("LOCAL_SCOPE_INVALID");
    const generation = this.generation;
    await this.identity(request, generation);
    const scopes = request.ids.map(
      (id) => this.scopes.get(id) ?? fail("LOCAL_SCOPE_REVOKED"),
    );
    // Consume before asynchronous reads so a second request cannot reuse a grant.
    for (const id of request.ids) this.scopes.delete(id);
    try {
      const files: File[] = [];
      for (const scope of scopes)
        await this.collect(scope, scope.path, files, generation);
      if (files.length === 0) fail("LOCAL_SCOPE_EMPTY");
      const contents: string[] = [];
      let characters = 0;
      const copy = desktopLocale(request.locale);
      for (const file of files) {
        await this.identity(request, generation);
        await this.checkScope(file.scope);
        if ((await realpath(file.path)) !== file.path)
          fail("LOCAL_SCOPE_UNSAFE_PATH");
        const handle = await open(
          file.path,
          constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
        );
        try {
          const before = await handle.stat();
          if (!sameFile(before, file.stat) || !before.isFile())
            fail("LOCAL_SCOPE_UNSAFE_PATH");
          if (before.size > MAX_BYTES) fail("LOCAL_SCOPE_LIMIT");
          const bytes = Buffer.alloc(MAX_BYTES + 1);
          let offset = 0;
          while (offset < bytes.length) {
            const { bytesRead } = await handle.read(
              bytes,
              offset,
              bytes.length - offset,
              offset,
            );
            if (bytesRead === 0) break;
            offset += bytesRead;
            this.active(generation);
          }
          if (offset > MAX_BYTES) fail("LOCAL_SCOPE_LIMIT");
          const after = await handle.stat();
          if (after.size !== before.size || after.mtimeMs !== before.mtimeMs)
            fail("LOCAL_SCOPE_UNSAFE_PATH");
          let content: string;
          try {
            content = new TextDecoder("utf-8", { fatal: true }).decode(
              bytes.subarray(0, offset),
            );
          } catch {
            fail("LOCAL_SCOPE_UNSUPPORTED_FILE");
          }
          if (content.includes("\0")) fail("LOCAL_SCOPE_UNSUPPORTED_FILE");
          characters += content.length;
          if (characters > MAX_CHARACTERS) fail("LOCAL_SCOPE_LIMIT");
          await this.checkScope(file.scope);
          if ((await realpath(file.path)) !== file.path)
            fail("LOCAL_SCOPE_UNSAFE_PATH");
          contents.push(
            `${copy.source}: ${JSON.stringify(
              basename(file.path),
            )}\n${content}`,
          );
        } finally {
          await handle.close();
        }
      }
      await this.identity(request, generation);
      return {
        text: `${copy.context}\n\n${contents.join("\n\n")}`,
        sources: files.map((file) => ({
          name: basename(file.path),
          path: file.path,
        })),
      };
    } catch (error) {
      this.readError(error);
    }
  }
}
