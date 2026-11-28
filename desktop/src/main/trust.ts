import type { ScopeSession } from "../shared/bridge.js";

export function normalizeServerURL(value: string): string {
  const url = new URL(value.trim());
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (
    (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new Error("INVALID_SERVER_URL");
  url.pathname = `${url.pathname.replace(/\/+$/, "")}/`;
  return url.href;
}

export function trustedPage(value: string, server: string): boolean {
  try {
    const page = new URL(value);
    const base = new URL(server);
    return (
      page.origin === base.origin &&
      page.pathname.startsWith(base.pathname) &&
      !page.username &&
      !page.password
    );
  } catch {
    return false;
  }
}

export async function verifySession(
  server: string,
  session: ScopeSession,
  request: typeof fetch = fetch,
): Promise<number> {
  const base = normalizeServerURL(server);
  const get = async (path: string): Promise<unknown> => {
    const response = await request(new URL(path, base), {
      headers: { Authorization: `Bearer ${session.accessToken}` },
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error("LOCAL_SCOPE_AUTH_FAILED");
    return response.json();
  };
  const user = (await get("api/auth/me")) as { id?: number };
  if (!Number.isSafeInteger(user?.id) || user.id! <= 0)
    throw new Error("LOCAL_SCOPE_AUTH_FAILED");
  await get(`api/agents/${encodeURIComponent(session.agentId)}`);
  if (!session.conversationId.startsWith("draft:")) {
    await get(
      `api/agents/${encodeURIComponent(
        session.agentId,
      )}/threads/${encodeURIComponent(session.conversationId)}/history?limit=1`,
    );
  }
  return user.id!;
}
