import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import {
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { isAbsolute, join } from "node:path";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import {
  buildSidecarEnv,
  ensureWizardPassword,
  portableLaunch,
  reserveRandomPort,
  SidecarSupervisor,
  windowsJobArguments,
} from "./sidecar.js";

test("the bundled launch pins new data and loopback without inherited Python or auth bypasses", () => {
  const portable = join(tmpdir(), "portable");
  const data = join(tmpdir(), "new-colink-server");
  const environment = buildSidecarEnv(
    {
      HOME: "/unchanged-home",
      OCTOP_HOME: "/old-data",
      OCTOP_DATABASE_SQLITE_PATH: "/old-data/octop.db",
      OCTOP_ACCESS_TOKEN_TTL: "999999",
      PYTHONPATH: "/old-python",
      PYTHONHOME: "/system-python",
      OCTOP_REQUIRE_SETUP_PASSWORD: "0",
    },
    { server: data, portable, port: 32123 },
  );
  assert.equal(environment.HOME, "/unchanged-home");
  assert.equal(environment.OCTOP_HOME, data);
  assert.equal(environment.OCTOP_BIND_HOST, "127.0.0.1");
  assert.equal(environment.OCTOP_PORT, "32123");
  assert.equal(environment.OCTOP_REQUIRE_SETUP_PASSWORD, "1");
  assert.equal(environment.PYTHONPATH, undefined);
  assert.equal(environment.PYTHONHOME, undefined);
  assert.equal(environment.OCTOP_GREEN_PACKAGES, join(portable, "packages"));
  assert.equal(environment.OCTOP_DATABASE_SQLITE_PATH, undefined);
  assert.equal(environment.OCTOP_ACCESS_TOKEN_TTL, undefined);
});

test("supervision waits for health and stops its own child process tree", async () => {
  const port = await reserveRandomPort();
  const worker = `require('node:http').createServer((q,r)=>{r.setHeader('content-type','application/json');r.end('{"ok":true}')}).listen(${port},'127.0.0.1')`;
  const parent = `require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(
    worker,
  )}],{stdio:'ignore'});setInterval(()=>{},1000);`;
  const sidecar = new SidecarSupervisor(
    {
      executable: process.execPath,
      args: ["-e", parent],
      cwd: tmpdir(),
      env: process.env,
      port,
    },
    () => {},
  );
  try {
    sidecar.start();
    await sidecar.waitUntilReady(5000);
    assert.equal((await fetch(`http://127.0.0.1:${port}/api/health`)).ok, true);
  } finally {
    await sidecar.stop();
  }
  await assert.rejects(fetch(`http://127.0.0.1:${port}/api/health`));
});

test("a crashed child is reported without waiting for the startup timeout", async () => {
  const port = await reserveRandomPort();
  const sidecar = new SidecarSupervisor(
    {
      executable: process.execPath,
      args: ["-e", "process.exit(1)"],
      cwd: tmpdir(),
      env: process.env,
      port,
    },
    () => {},
  );
  sidecar.start();
  await assert.rejects(sidecar.waitUntilReady(1000), /SIDECAR_START_FAILED/);
  await sidecar.stop();
});

test(
  "POSIX cleanup also stops descendants after the parent has already exited",
  { skip: process.platform === "win32" },
  async () => {
    const dir = await realpath(
      await mkdtemp(join(tmpdir(), "colink-crashed-tree-")),
    );
    const port = await reserveRandomPort();
    const pidFile = join(dir, "parent-pid");
    const worker = `require('node:http').createServer((q,r)=>r.end('{"ok":true}')).listen(${port},'127.0.0.1')`;
    const parent = `require('node:fs').writeFileSync(${JSON.stringify(
      pidFile,
    )},String(process.pid));require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(
      worker,
    )}],{stdio:'ignore'});setTimeout(()=>process.exit(1),800);`;
    let exited!: () => void;
    const exit = new Promise<void>((resolve) => {
      exited = resolve;
    });
    const sidecar = new SidecarSupervisor(
      {
        executable: process.execPath,
        args: ["-e", parent],
        cwd: dir,
        env: process.env,
        port,
      },
      exited,
    );
    try {
      sidecar.start();
      await sidecar.waitUntilReady(3000);
      await exit;
      await sidecar.stop();
      let listening = true;
      const deadline = Date.now() + 500;
      while (listening && Date.now() < deadline) {
        try {
          await fetch(`http://127.0.0.1:${port}/api/health`);
          await delay(20);
        } catch {
          listening = false;
        }
      }
      assert.equal(listening, false);
    } finally {
      try {
        process.kill(-Number(await readFile(pidFile, "utf8")), "SIGKILL");
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
      }
      await rm(dir, { recursive: true, force: true });
    }
  },
);

test(
  "Windows closes the real child process tree when its Python parent crashes",
  {
    skip: process.platform !== "win32" && "requires Windows Job Objects",
    timeout: 20_000,
  },
  async (t) => {
    const python =
      process.env.COLINK_TEST_PYTHON ||
      join(
        __dirname,
        "../../../portable/release",
        `Octop-windows-${process.arch === "arm64" ? "arm64" : "amd64"}`,
        "runtime/python.exe",
      );
    assert.equal(isAbsolute(python), true);
    if (process.env.COLINK_TEST_PYTHON) assert.equal(existsSync(python), true);
    if (!existsSync(python)) {
      t.skip(
        "Build the portable runtime or set COLINK_TEST_PYTHON to its absolute executable path",
      );
      return;
    }
    const dir = await realpath(
      await mkdtemp(join(tmpdir(), "colink-windows-job-")),
    );
    const port = await reserveRandomPort();
    const pidFile = join(dir, "worker-pid");
    const parentFile = join(dir, "crash-parent.py");
    const worker = `require('node:http').createServer((q,r)=>r.end('{"ok":true}')).listen(${port},'127.0.0.1',()=>require('node:fs').writeFileSync(${JSON.stringify(
      pidFile,
    )},String(process.pid)))`;
    await writeFile(
      parentFile,
      `import os, subprocess, sys, time
subprocess.Popen([sys.argv[1], "-e", sys.argv[2]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
deadline = time.monotonic() + 10
while not os.path.exists(sys.argv[3]):
    if time.monotonic() >= deadline:
        raise RuntimeError("child did not start")
    time.sleep(0.02)
os._exit(23)
`,
    );
    const parent = spawn(
      python,
      windowsJobArguments(parentFile, [process.execPath, worker, pidFile]),
      { cwd: dir, stdio: ["ignore", "ignore", "pipe"], windowsHide: true },
    );
    let stderr = "";
    const abort = (): void => {
      parent.kill();
    };
    t.signal.addEventListener("abort", abort, { once: true });
    parent.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    try {
      const code = await new Promise<number | null>((resolve, reject) => {
        parent.once("error", reject);
        parent.once("exit", resolve);
      });
      assert.equal(code, 23, stderr);
      assert.equal(existsSync(pidFile), true);
      let listening = true;
      const deadline = Date.now() + 3000;
      while (listening && Date.now() < deadline) {
        try {
          await fetch(`http://127.0.0.1:${port}/api/health`, {
            signal: AbortSignal.timeout(500),
          });
          await delay(20);
        } catch {
          listening = false;
        }
      }
      assert.equal(
        listening,
        false,
        "the child must die without taskkill or supervisor cleanup",
      );
    } finally {
      t.signal.removeEventListener("abort", abort);
      parent.kill();
      if (existsSync(pidFile)) {
        try {
          process.kill(Number(await readFile(pidFile, "utf8")), "SIGKILL");
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
        }
      }
      await rm(dir, { recursive: true, force: true });
    }
  },
);

test("a fresh wizard secret never reuses the OS home secret and survives restart", async () => {
  const dir = await realpath(await mkdtemp(join(tmpdir(), "colink-sidecar-")));
  try {
    const server = join(dir, ".colink-v1", "server");
    await mkdir(server, { recursive: true });
    await writeFile(join(dir, "octop-login.txt"), "old-personal-secret\n");
    await ensureWizardPassword(server);
    const secret = await readFile(join(server, "octop-login.txt"), "utf8");
    assert.notEqual(secret, "old-personal-secret\n");
    assert.match(secret, /^[A-Za-z0-9_-]{43}\n$/);
    await ensureWizardPassword(server);
    assert.equal(
      await readFile(join(server, "octop-login.txt"), "utf8"),
      secret,
    );
    assert.equal(
      await readFile(join(dir, "octop-login.txt"), "utf8"),
      "old-personal-secret\n",
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("a missing bundled interpreter never falls back to system Python", async () => {
  await assert.rejects(
    portableLaunch(join(tmpdir(), "missing-colink-bundle")),
    /BUNDLED_RUNTIME_MISSING/,
  );
});
