import assert from "node:assert/strict";
import {
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { LocalFiles } from "./local-files.js";

const session = {
  accessToken: "test-token",
  agentId: "expert",
  conversationId: "draft:test",
  locale: "zh" as const,
};

test("selected Chinese text is used once without changing or uploading its original", async () => {
  const dir = await realpath(await mkdtemp(join(tmpdir(), "colink-files-")));
  try {
    const file = join(dir, "会议纪要.txt");
    await writeFile(file, "下周三提交报告。");
    const local = new LocalFiles(async () => 7);
    const scopes = await local.select(
      { ...session, kind: "files" },
      async () => [file],
    );
    assert.equal(scopes[0]?.name, "会议纪要.txt");
    const result = await local.consume({
      ...session,
      ids: scopes.map((s) => s.id),
    });
    assert.match(result.text, /下周三提交报告。/);
    assert.equal(result.sources[0]?.path, file);
    assert.equal(await readFile(file, "utf8"), "下周三提交报告。");
    await assert.rejects(
      local.consume({ ...session, ids: scopes.map((s) => s.id) }),
      /LOCAL_SCOPE_REVOKED/,
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

async function fixture(
  run: (file: string, dir: string) => Promise<void>,
): Promise<void> {
  const dir = await realpath(await mkdtemp(join(tmpdir(), "colink-scopes-")));
  try {
    const file = join(dir, "资料.txt");
    await writeFile(file, "仅在本机选择的文本");
    await run(file, dir);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

test("an unauthenticated caller cannot open the picker", async () => {
  let opened = false;
  const local = new LocalFiles(async () => {
    throw new Error("unauthorized");
  });
  await assert.rejects(
    local.select({ ...session, kind: "files" }, async () => {
      opened = true;
      return [];
    }),
    /LOCAL_SCOPE_AUTH_FAILED/,
  );
  assert.equal(opened, false);
});

test("a different verified account cannot consume the previous account's selection", async () =>
  fixture(async (file) => {
    let userId = 7;
    const local = new LocalFiles(async () => userId);
    const scopes = await local.select(
      { ...session, kind: "files" },
      async () => [file],
    );
    userId = 8;
    await assert.rejects(
      local.consume({ ...session, ids: [scopes[0]!.id] }),
      /LOCAL_SCOPE_IDENTITY_CHANGED/,
    );
    userId = 7;
    await assert.rejects(
      local.consume({ ...session, ids: [scopes[0]!.id] }),
      /LOCAL_SCOPE_REVOKED/,
    );
  }));

test("changing the agent or conversation revokes old selections", async () =>
  fixture(async (file) => {
    for (const changed of [
      { agentId: "other-agent" },
      { conversationId: "draft:other" },
    ]) {
      const local = new LocalFiles(async () => 7);
      const [scope] = await local.select(
        { ...session, kind: "files" },
        async () => [file],
      );
      await assert.rejects(
        local.consume({ ...session, ...changed, ids: [scope!.id] }),
        /LOCAL_SCOPE_IDENTITY_CHANGED/,
      );
    }
  }));

test("concurrent selections cannot mix different accounts, agents or conversations", async () =>
  fixture(async (file) => {
    for (const changed of [
      { accessToken: "other-account" },
      { agentId: "other-agent" },
      { conversationId: "draft:other" },
    ]) {
      const other = { ...session, ...changed };
      const local = new LocalFiles(async (identity) =>
        identity.accessToken === "other-account" ? 8 : 7,
      );
      const results = await Promise.allSettled([
        local.select({ ...session, kind: "files" }, async () => [file]),
        local.select({ ...other, kind: "files" }, async () => [file]),
      ]);
      assert.ok(results.some((result) => result.status === "rejected"));
      for (const [index, result] of results.entries()) {
        if (result.status !== "fulfilled") continue;
        await assert.rejects(
          local.consume({
            ...(index === 0 ? other : session),
            ids: result.value.map((scope) => scope.id),
          }),
          /LOCAL_SCOPE_(IDENTITY_CHANGED|REVOKED)/,
        );
      }
    }
  }));

test("concurrent selections for the same session remain usable together", async () =>
  fixture(async (file, dir) => {
    const second = join(dir, "第二份.txt");
    await writeFile(second, "同一会话的另一份资料");
    const local = new LocalFiles(async () => 7);
    const selections = await Promise.all([
      local.select({ ...session, kind: "files" }, async () => [file]),
      local.select({ ...session, kind: "files" }, async () => [second]),
    ]);
    const context = await local.consume({
      ...session,
      ids: selections.flat().map((scope) => scope.id),
    });
    assert.match(context.text, /仅在本机选择的文本/);
    assert.match(context.text, /同一会话的另一份资料/);
    assert.equal(context.sources.length, 2);
  }));

test("disconnecting revokes scopes even after the server becomes reachable again", async () =>
  fixture(async (file) => {
    let connected = true;
    const local = new LocalFiles(async () => {
      if (!connected) throw new Error("offline");
      return 7;
    });
    const [scope] = await local.select(
      { ...session, kind: "files" },
      async () => [file],
    );
    connected = false;
    await assert.rejects(
      local.consume({ ...session, ids: [scope!.id] }),
      /LOCAL_SCOPE_AUTH_FAILED/,
    );
    connected = true;
    await assert.rejects(
      local.consume({ ...session, ids: [scope!.id] }),
      /LOCAL_SCOPE_REVOKED/,
    );
  }));

test("removed selections cannot be read", async () =>
  fixture(async (file) => {
    const local = new LocalFiles(async () => 7);
    const [scope] = await local.select(
      { ...session, kind: "files" },
      async () => [file],
    );
    local.remove(scope!.id);
    await assert.rejects(
      local.consume({ ...session, ids: [scope!.id] }),
      /LOCAL_SCOPE_REVOKED/,
    );
  }));

test("an account change while the native picker is open cannot grant the chosen file", async () =>
  fixture(async (file) => {
    let userId = 7;
    const local = new LocalFiles(async () => userId);
    await assert.rejects(
      local.select({ ...session, kind: "files" }, async () => {
        userId = 8;
        return [file];
      }),
      /LOCAL_SCOPE_IDENTITY_CHANGED/,
    );
  }));

test("clearing while the native picker is open cannot restore a revoked grant", async () =>
  fixture(async (file) => {
    const local = new LocalFiles(async () => 7);
    await assert.rejects(
      local.select({ ...session, kind: "files" }, async () => {
        local.clear();
        return [file];
      }),
      /LOCAL_SCOPE_REVOKED/,
    );
  }));

test("a directory with 21 files fails explicitly instead of returning the first 20", async () =>
  fixture(async (_file, dir) => {
    const folder = join(dir, "selected");
    await mkdir(folder);
    await Promise.all(
      Array.from({ length: 21 }, (_, i) =>
        writeFile(join(folder, `${i}.txt`), `${i}`),
      ),
    );
    const local = new LocalFiles(async () => 7);
    const [scope] = await local.select(
      { ...session, kind: "directory" },
      async () => [folder],
    );
    await assert.rejects(
      local.consume({ ...session, ids: [scope!.id] }),
      /LOCAL_SCOPE_LIMIT/,
    );
  }));

test("clearing while a read awaits verification prevents the text from returning", async () =>
  fixture(async (file) => {
    let pause: Promise<void> = Promise.resolve();
    const local = new LocalFiles(async () => {
      await pause;
      return 7;
    });
    const [scope] = await local.select(
      { ...session, kind: "files" },
      async () => [file],
    );
    let release!: () => void;
    pause = new Promise<void>((resolve) => {
      release = resolve;
    });
    const reading = local.consume({ ...session, ids: [scope!.id] });
    local.clear();
    release();
    await assert.rejects(reading, /LOCAL_SCOPE_REVOKED/);
  }));

test("a replaced file cannot reuse the prior selection", async () =>
  fixture(async (file, dir) => {
    const local = new LocalFiles(async () => 7);
    const [scope] = await local.select(
      { ...session, kind: "files" },
      async () => [file],
    );
    await rename(file, join(dir, "original.txt"));
    await writeFile(file, "another file");
    await assert.rejects(
      local.consume({ ...session, ids: [scope!.id] }),
      /LOCAL_SCOPE_UNSAFE_PATH/,
    );
  }));

test(
  "a selected directory never follows a symbolic link outside its scope",
  { skip: process.platform === "win32" },
  async () =>
    fixture(async (file, dir) => {
      const folder = join(dir, "selected");
      await mkdir(folder);
      await symlink(file, join(folder, "linked.txt"));
      const local = new LocalFiles(async () => 7);
      const [scope] = await local.select(
        { ...session, kind: "directory" },
        async () => [folder],
      );
      await assert.rejects(
        local.consume({ ...session, ids: [scope!.id] }),
        /LOCAL_SCOPE_UNSAFE_PATH/,
      );
    }),
);

test("unsupported formats and oversized text fail without partial text", async () =>
  fixture(async (_file, dir) => {
    for (const [name, content, error] of [
      ["office.docx", "zip", /LOCAL_SCOPE_UNSUPPORTED_FILE/],
      ["large.txt", "x".repeat(60_001), /LOCAL_SCOPE_LIMIT/],
      ["binary.txt", "\0binary", /LOCAL_SCOPE_UNSUPPORTED_FILE/],
    ] as const) {
      const path = join(dir, name);
      await writeFile(path, content);
      const local = new LocalFiles(async () => 7);
      const [scope] = await local.select(
        { ...session, kind: "files" },
        async () => [path],
      );
      await assert.rejects(
        local.consume({ ...session, ids: [scope!.id] }),
        error,
      );
    }
  }));
