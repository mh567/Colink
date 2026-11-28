import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import { normalizeServerURL, trustedPage, verifySession } from "./trust.js";

test("server trust accepts enterprise HTTPS and only loopback HTTP", () => {
  assert.equal(
    normalizeServerURL("https://colink.example.com/office"),
    "https://colink.example.com/office/",
  );
  for (const value of [
    "http://example.com",
    "file:///tmp/file",
    "https://user:secret@example.com",
    "https://example.com?secret=x",
  ])
    assert.throws(() => normalizeServerURL(value));
  assert.equal(
    trustedPage(
      "https://colink.example.com/office/chat",
      "https://colink.example.com/office/",
    ),
    true,
  );
  for (const value of [
    "https://colink.example.com/office-evil/",
    "https://evil.example.com/office/",
    "https://colink.example.com/office/../admin",
  ])
    assert.equal(
      trustedPage(value, "https://colink.example.com/office/"),
      false,
    );
});

test("session verification uses the server identity and checks agent and existing thread access", async () => {
  const requests: string[] = [];
  const server = createServer((request, response) => {
    requests.push(request.url!);
    response.setHeader("Content-Type", "application/json");
    if (request.headers.authorization !== "Bearer valid-token") {
      response.writeHead(401);
      response.end("{}");
      return;
    }
    if (request.url === "/office/api/auth/me") response.end('{"id":12}');
    else if (request.url === "/office/api/agents/allowed")
      response.end('{"agent_id":"allowed"}');
    else if (
      request.url === "/office/api/agents/allowed/threads/mine/history?limit=1"
    )
      response.end('{"items":[]}');
    else {
      response.writeHead(403);
      response.end("{}");
    }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    if (!address || typeof address === "string")
      throw new Error("missing test port");
    const url = `http://127.0.0.1:${address.port}/office/`;
    const session = {
      accessToken: "valid-token",
      agentId: "allowed",
      conversationId: "mine",
    };
    assert.equal(await verifySession(url, session), 12);
    assert.deepEqual(requests, [
      "/office/api/auth/me",
      "/office/api/agents/allowed",
      "/office/api/agents/allowed/threads/mine/history?limit=1",
    ]);
    await assert.rejects(
      verifySession(url, { ...session, accessToken: "expired" }),
    );
    await assert.rejects(
      verifySession(url, { ...session, agentId: "someone-else" }),
    );
    await assert.rejects(
      verifySession(url, { ...session, conversationId: "other-thread" }),
    );
    assert.equal(
      await verifySession(url, {
        ...session,
        conversationId: "draft:local-new",
      }),
      12,
    );
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  }
});
