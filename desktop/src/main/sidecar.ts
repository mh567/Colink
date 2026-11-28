// Process supervision adapted from TencentCloud/Octop v1.0.3b7 (MIT).
import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { lstat, realpath, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { isAbsolute, join, relative, sep } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

export function buildSidecarEnv(
  base: NodeJS.ProcessEnv,
  options: { server: string; portable: string; port: number },
): NodeJS.ProcessEnv {
  const env = { ...base };
  for (const key of Object.keys(env))
    if (key.startsWith("OCTOP_")) delete env[key];
  env.OCTOP_HOME = options.server;
  env.OCTOP_GREEN_PACKAGES = join(options.portable, "packages");
  env.OCTOP_BIND_HOST = "127.0.0.1";
  env.OCTOP_PORT = String(options.port);
  env.OCTOP_REQUIRE_SETUP_PASSWORD = "1";
  env.PYTHONNOUSERSITE = "1";
  env.PYTHONPYCACHEPREFIX = join(options.server, "pyc");
  delete env.PYTHONPATH;
  delete env.PYTHONHOME;
  delete env.OCTOP_DESKTOP_SHELL;
  return env;
}

export async function ensureWizardPassword(server: string): Promise<void> {
  const target = join(server, "octop-login.txt");
  try {
    await writeFile(target, `${randomBytes(32).toString("base64url")}\n`, {
      flag: "wx",
      mode: 0o600,
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    const file = await lstat(target);
    if (file.isSymbolicLink() || !file.isFile() || file.size === 0)
      throw new Error("UNSAFE_DATA_DIRECTORY");
  }
}

const WINDOWS_JOB_BOOTSTRAP = String.raw`
import ctypes, runpy, sys
from ctypes import wintypes

class BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]

class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]

class ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", BasicLimitInformation),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wintypes.HANDLE

# A non-inherited handle closes on every parent exit, including crashes.
job = kernel32.CreateJobObjectW(None, None)
if not job:
    raise ctypes.WinError(ctypes.get_last_error())
limits = ExtendedLimitInformation()
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9
limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
if not kernel32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(limits), ctypes.sizeof(limits)):
    raise ctypes.WinError(ctypes.get_last_error())
if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
    raise ctypes.WinError(ctypes.get_last_error())
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
`;

export function windowsJobArguments(launch: string, args: string[]): string[] {
  return ["-s", "-c", WINDOWS_JOB_BOOTSTRAP, launch, ...args];
}

export async function portableLaunch(
  portable: string,
): Promise<{ executable: string; args: string[]; cwd: string }> {
  try {
    const root = await realpath(portable);
    const executable = join(
      root,
      "runtime",
      ...(process.platform === "win32" ? ["python.exe"] : ["bin", "python3"]),
    );
    const launch = join(root, "launch.py");
    for (const file of [executable, launch]) {
      const path = await realpath(file);
      const part = relative(root, path);
      if (
        part === ".." ||
        part.startsWith(`..${sep}`) ||
        isAbsolute(part) ||
        !(await stat(path)).isFile()
      )
        throw new Error("outside-bundle");
    }
    if (!(await stat(join(root, "packages"))).isDirectory())
      throw new Error("missing-packages");
    return {
      executable,
      args:
        process.platform === "win32"
          ? windowsJobArguments(launch, ["run"])
          : ["-s", launch, "run"],
      cwd: root,
    };
  } catch {
    throw new Error("BUNDLED_RUNTIME_MISSING");
  }
}

export function reserveRandomPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("PORT_UNAVAILABLE"));
        return;
      }
      server.close((error) => (error ? reject(error) : resolve(address.port)));
    });
  });
}

async function signalTree(child: ChildProcess, force: boolean): Promise<void> {
  if (child.pid === undefined) return;
  if (process.platform === "win32") {
    await new Promise<void>((resolve, reject) => {
      const task = spawn(
        join(
          process.env.SystemRoot || "C:\\Windows",
          "System32",
          "taskkill.exe",
        ),
        ["/pid", String(child.pid), "/T", ...(force ? ["/F"] : [])],
        { stdio: "ignore", windowsHide: true },
      );
      task.once("error", reject);
      task.once("exit", () => resolve());
    });
  } else {
    try {
      process.kill(-child.pid, force ? "SIGKILL" : "SIGTERM");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
    }
  }
}

export class SidecarSupervisor {
  private child: ChildProcess | undefined;
  private exited = false;
  private stopping = false;

  constructor(
    private readonly launch: {
      executable: string;
      args: string[];
      cwd: string;
      env: NodeJS.ProcessEnv;
      port: number;
    },
    private readonly onExit: () => void,
  ) {}

  start(): void {
    if (this.child) throw new Error("SIDECAR_ALREADY_STARTED");
    this.child = spawn(this.launch.executable, this.launch.args, {
      cwd: this.launch.cwd,
      env: this.launch.env,
      detached: process.platform !== "win32",
      stdio: "ignore",
      windowsHide: true,
    });
    const stopped = (): void => {
      if (this.exited) return;
      this.exited = true;
      if (!this.stopping) this.onExit();
    };
    this.child.once("error", stopped);
    this.child.once("exit", stopped);
  }

  async waitUntilReady(timeout = 180_000): Promise<void> {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline && !this.exited) {
      try {
        const response = await fetch(
          `http://127.0.0.1:${this.launch.port}/api/health`,
          { redirect: "error", signal: AbortSignal.timeout(2000) },
        );
        const body = (await response.json()) as { ok?: boolean };
        if (response.ok && body.ok === true && !this.exited) return;
      } catch {
        /* The child may still be importing its bundled dependencies. */
      }
      await delay(250);
    }
    throw new Error("SIDECAR_START_FAILED");
  }

  async stop(): Promise<void> {
    this.stopping = true;
    if (!this.child) return;
    if (this.exited) {
      if (process.platform !== "win32") await signalTree(this.child, true);
      return;
    }
    await signalTree(this.child, false);
    const deadline = Date.now() + 5000;
    while (!this.exited && Date.now() < deadline) await delay(50);
    if (!this.exited) {
      await signalTree(this.child, true);
      const forceDeadline = Date.now() + 2000;
      while (!this.exited && Date.now() < forceDeadline) await delay(50);
    }
    if (!this.exited) throw new Error("SIDECAR_STOP_FAILED");
    if (process.platform !== "win32") await signalTree(this.child, true);
  }
}
