import { lstatSync, mkdirSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from "node:path";

export interface DataPaths {
  root: string;
  profile: string;
  server: string;
}

function inside(path: string, root: string): boolean {
  const rel = relative(root, path);
  return (
    rel === "" ||
    (!rel.startsWith(`..${sep}`) && rel !== ".." && !isAbsolute(rel))
  );
}

function ensureDirectory(path: string): void {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  const entry = lstatSync(path);
  if (!entry.isDirectory() || entry.isSymbolicLink())
    throw new Error("UNSAFE_DATA_DIRECTORY");
}

function resolveExistingParents(path: string): string {
  try {
    return realpathSync(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    return join(resolveExistingParents(dirname(path)), basename(path));
  }
}

export function createDataPaths(
  env: NodeJS.ProcessEnv,
  home = homedir(),
): DataPaths {
  const root = resolve(env.COLINK_DESKTOP_HOME || join(home, ".colink-v1"));
  const legacy = [join(home, ".octop"), env.OCTOP_HOME].filter(
    (p): p is string => Boolean(p),
  );
  if (
    legacy.some((p) => inside(root, resolve(p))) ||
    root.split(sep).includes(".octop")
  ) {
    throw new Error("LEGACY_DATA_DIRECTORY");
  }
  const canonical = resolveExistingParents(root);
  const legacyTargets = legacy.map((p) => resolveExistingParents(resolve(p)));
  if (
    legacyTargets.some((p) => inside(canonical, p)) ||
    canonical.split(sep).includes(".octop")
  ) {
    throw new Error("LEGACY_DATA_DIRECTORY");
  }
  ensureDirectory(root);
  const paths = {
    root: canonical,
    profile: join(canonical, "profile"),
    server: join(canonical, "server"),
  };
  ensureDirectory(paths.profile);
  ensureDirectory(paths.server);
  return paths;
}
