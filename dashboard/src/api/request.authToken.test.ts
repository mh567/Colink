import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

vi.mock("../i18n", () => ({
  default: { language: "zh" },
}));

describe("auth token remember / session-only", () => {
  let mod: typeof import("./request");

  beforeEach(async () => {
    vi.resetModules();
    localStorage.clear();
    sessionStorage.clear();
    mod = await import("./request");
  });

  it("defaults to a remembered (persistent) token in localStorage", () => {
    mod.setAuthToken("tok");
    expect(mod.getAuthToken()).toBe("tok");
    expect(mod.isSessionOnlyAuth()).toBe(false);
    expect(localStorage.getItem("auth_token")).toBe("tok");
    expect(sessionStorage.getItem("auth_token")).toBeNull();
  });

  it("stores a session-only token in sessionStorage", () => {
    mod.setAuthToken("tok", false);
    expect(mod.getAuthToken()).toBe("tok");
    expect(mod.isSessionOnlyAuth()).toBe(true);
    expect(sessionStorage.getItem("auth_token")).toBe("tok");
    expect(localStorage.getItem("auth_token")).toBeNull();
  });

  it("clears the other store when switching remember mode", () => {
    mod.setAuthToken("a", false);
    mod.setAuthToken("b", true);
    expect(localStorage.getItem("auth_token")).toBe("b");
    expect(sessionStorage.getItem("auth_token")).toBeNull();
    expect(mod.isSessionOnlyAuth()).toBe(false);
  });

  it("preserves session-only mode when renewing the access token", () => {
    mod.setAuthToken("old", false);
    const response = new Response(null, {
      headers: { "X-Octop-Access-Token": "renewed" },
    });
    mod.applyRenewedAccessToken(response, { Authorization: "Bearer old" });
    expect(mod.getAuthToken()).toBe("renewed");
    expect(mod.isSessionOnlyAuth()).toBe(true);
    expect(sessionStorage.getItem("auth_token")).toBe("renewed");
  });

  it("reads and writes the SSO remember preference", () => {
    expect(mod.getRememberLoginPreference()).toBe(true);
    mod.setRememberLoginPreference(false);
    expect(mod.getRememberLoginPreference()).toBe(false);
  });

  it("clearAuthToken removes both stores", () => {
    mod.setAuthToken("tok", false);
    mod.clearAuthToken();
    expect(mod.getAuthToken()).toBe("");
    expect(sessionStorage.getItem("auth_token")).toBeNull();
  });
});

describe("renewals belong to the request's login session", () => {
  let mod: typeof import("./request");
  let pending: Array<{
    headers: Headers;
    finish: (response: Response) => void;
  }>;
  const renewed = vi.fn();
  const kinds = [
    "request",
    "requestBlob",
    "probeAuthResource",
    "requestStream",
    "requestUpload",
  ] as const;

  beforeEach(async () => {
    vi.resetModules();
    localStorage.clear();
    sessionStorage.clear();
    mod = await import("./request");
    pending = [];
    renewed.mockClear();
    window.addEventListener(mod.AUTH_TOKEN_RENEWED_EVENT, renewed);
    const enqueue = (headers: HeadersInit) =>
      new Promise<Response>((finish) => {
        pending.push({ headers: new Headers(headers), finish });
      });
    vi.stubGlobal(
      "fetch",
      vi.fn((_url, options) => enqueue(options.headers)),
    );
    vi.stubGlobal(
      "XMLHttpRequest",
      class {
        headers = new Headers();
        responseHeaders = new Headers();
        status = 0;
        responseText = "";
        onload?: () => void;
        open() {}
        setRequestHeader(name: string, value: string) {
          this.headers.append(name, value);
        }
        getResponseHeader(name: string) {
          return this.responseHeaders.get(name);
        }
        send() {
          void enqueue(this.headers).then(async (response) => {
            this.status = response.status;
            this.responseHeaders = response.headers;
            this.responseText = await response.text();
            this.onload?.();
          });
        }
      },
    );
  });

  afterEach(() => {
    window.removeEventListener(mod.AUTH_TOKEN_RENEWED_EVENT, renewed);
    vi.unstubAllGlobals();
  });

  function start(kind: (typeof kinds)[number], options: RequestInit = {}) {
    return kind === "requestUpload"
      ? mod.requestUpload("/files", new FormData(), options)
      : mod[kind]("/files", options);
  }

  function response(token: string) {
    return Response.json(
      { ok: true },
      {
        headers: { "X-Octop-Access-Token": token },
      },
    );
  }

  describe.each(kinds)("%s", (kind) => {
    it.each(["switch", "logout"])(
      "ignores an old response after %s",
      async (change) => {
        mod.setAuthToken("account-A");
        const request = start(kind);
        expect(pending[0].headers.get("Authorization")).toBe(
          "Bearer account-A",
        );
        if (change === "switch") mod.setAuthToken("account-B", false);
        else mod.clearAuthToken();
        pending[0].finish(response("account-A-renewed"));
        await request;
        expect(mod.getAuthToken()).toBe(change === "switch" ? "account-B" : "");
        expect(renewed).not.toHaveBeenCalled();
      },
    );

    it("keeps the first valid renewal when concurrent responses arrive", async () => {
      mod.setAuthToken("account-A", false);
      const first = start(kind);
      const second = start(kind);
      pending[0].finish(response("account-A-renewed"));
      await first;
      pending[1].finish(response("account-A-older-renewal"));
      await second;
      expect(mod.getAuthToken()).toBe("account-A-renewed");
      expect(mod.isSessionOnlyAuth()).toBe(true);
      expect(renewed).toHaveBeenCalledOnce();
      expect((renewed.mock.calls[0][0] as CustomEvent).detail).toEqual({
        previousToken: "account-A",
        accessToken: "account-A-renewed",
      });
    });

    it("does not renew the login using a caller-supplied credential", async () => {
      mod.setAuthToken("account-A");
      const request = start(kind, {
        headers: { Authorization: "Bearer custom-token" },
      });
      pending[0].finish(response("custom-token-renewed"));
      await request;
      expect(mod.getAuthToken()).toBe("account-A");
      expect(renewed).not.toHaveBeenCalled();
    });
  });
});
