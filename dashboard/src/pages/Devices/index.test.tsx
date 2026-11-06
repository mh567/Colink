import { Suspense, useState } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { OctopUser } from "../../api/modules/auth";
import {
  CurrentUserProvider,
  useSetCurrentUser,
} from "../../hooks/useCurrentUser";
import { routeConfigs } from "../../routes";
import AuthGuard from "../../components/AuthGuard";
import { applyRenewedAccessToken } from "../../api/request";

vi.mock("../../i18n", () => ({ default: { language: "zh" } }));
vi.mock("../../utils/locale", () => ({
  applyUserLocale: () => Promise.resolve("zh"),
}));

type DesktopWindow = Window & {
  _wails?: { invoke: (message: string) => void };
};

const employee: OctopUser = {
  id: 2,
  username: "ada",
  role: "user",
  display_name: "Ada",
  locale: "zh",
  permissions: [],
};

const ownDevice = {
  device_id: "device-ada",
  owner_user_id: 2,
  owner_username: "ada",
  name: "Ada 的工作电脑",
  platform: "linux",
  status: "pending",
  online: false,
  last_seen: null,
  created_at: 1735689600,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const getStyle = window.getComputedStyle.bind(window);

function renderPage(user = employee) {
  return render(
    <CurrentUserProvider user={user} setUser={vi.fn()}>
      <Suspense fallback={<div>加载中</div>}>
        {routeConfigs.find((route) => route.path === "/devices")?.element}
      </Suspense>
    </CurrentUserProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("getComputedStyle", (element: Element) => getStyle(element));
  vi.stubGlobal("BASE_URL", "");
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem("auth_token", "employee-token");
});

afterEach(() => {
  delete (window as DesktopWindow)._wails;
  vi.unstubAllGlobals();
});

describe("设备页", () => {
  it("普通用户查看本人设备，浏览器说明桌面客户端要求并真实显示刷新失败", async () => {
    let fail = false;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/settings/timezone") {
        return json({ timezone: "Asia/Shanghai" });
      }
      if (url === "/api/devices") {
        return fail
          ? json(
              { error: { code: "BAD_REQUEST", message: "设备服务暂不可用" } },
              400,
            )
          : json([ownDevice]);
      }
      throw new Error(`Unexpected HTTP request: ${url}`);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "我的设备" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Ada 的工作电脑")).toBeInTheDocument();
    expect(
      screen.getByText(/请使用桌面客户端接入当前设备/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "接入当前设备" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "设备审批" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("待批准")).toBeInTheDocument();
    expect(screen.getByText("离线")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(/08:00:00/)).toBeInTheDocument(),
    );
    expect(
      fetcher.mock.calls.every(([url]) => !String(url).includes("/manage")),
    ).toBe(true);

    fail = true;
    await user.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByText("设备服务暂不可用")).toBeInTheDocument();
  });

  it("管理员单独审批他人设备，批准后仍等待真实在线状态，撤销后刷新", async () => {
    let status = "pending";
    const fetcher = vi.fn(
      async (input: RequestInfo | URL, options?: RequestInit) => {
        const url = String(input);
        if (url === "/api/settings/timezone")
          return json({ timezone: "Asia/Shanghai" });
        if (url === "/api/devices") return json([]);
        const managed = {
          ...ownDevice,
          device_id: "device/bob",
          owner_user_id: 3,
          owner_username: "bob",
          name: "Bob 的电脑",
          status,
        };
        if (url === "/api/devices/manage") return json([managed]);
        if (
          url === "/api/devices/device%2Fbob/approve" &&
          options?.method === "POST"
        ) {
          status = "approved";
          return json({ ...managed, status });
        }
        if (
          url === "/api/devices/device%2Fbob/revoke" &&
          options?.method === "POST"
        ) {
          status = "revoked";
          return json({ ...managed, status });
        }
        throw new Error(`Unexpected HTTP request: ${options?.method} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    renderPage({ ...employee, id: 1, username: "admin", role: "admin" });

    const row = (await screen.findByText("Bob 的电脑")).closest("tr")!;
    expect(
      await screen.findByRole("heading", { name: "设备审批" }),
    ).toBeInTheDocument();
    expect(
      within(row).queryByRole("button", { name: /接入/ }),
    ).not.toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "批准" }));
    expect(
      await screen.findByText(/设备编号：device\/bob/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认批准" }));
    expect(await within(row).findByText("已批准")).toBeInTheDocument();
    expect(within(row).getByText("离线")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "撤销" }));
    await user.click(await screen.findByRole("button", { name: "确认撤销" }));
    expect(await within(row).findByText("已撤销")).toBeInTheDocument();
  });

  it("审批 API 拒绝时保留真实状态并呈现服务端原因", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/settings/timezone")
          return json({ timezone: "Asia/Shanghai" });
        if (url === "/api/devices") return json([]);
        if (url === "/api/devices/manage") return json([ownDevice]);
        return json(
          { error: { code: "CONFLICT", message: "设备已被撤销，请重新接入" } },
          409,
        );
      }),
    );
    renderPage({ ...employee, id: 1, username: "admin", role: "admin" });
    const user = userEvent.setup();
    const row = (await screen.findByText("Ada 的工作电脑")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "批准" }));
    await user.click(await screen.findByRole("button", { name: "确认批准" }));
    expect(
      await screen.findByText("设备已被撤销，请重新接入"),
    ).toBeInTheDocument();
    expect(within(row).getByText("待批准")).toBeInTheDocument();
  });

  it("桌面客户端仅显式接入本机，显示 native 设备编号、真实状态和错误，断开等待回执", async () => {
    const invoke = vi.fn();
    (window as DesktopWindow)._wails = { invoke };
    let registered = false;
    let online = false;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/setup/status") return json({ setup_required: false });
      if (url === "/api/auth/me") return json(employee);
      if (url === "/api/settings/timezone")
        return json({ timezone: "Asia/Shanghai" });
      if (url === "/api/devices")
        return json(
          registered
            ? [
                {
                  ...ownDevice,
                  status: online ? "approved" : "pending",
                  online,
                },
              ]
            : [],
        );
      throw new Error(`Unexpected HTTP request: ${url}`);
    });
    vi.stubGlobal("fetch", fetcher);
    render(
      <MemoryRouter>
        <AuthGuard>
          <Suspense fallback={null}>
            {routeConfigs.find((route) => route.path === "/devices")?.element}
          </Suspense>
        </AuthGuard>
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    const connect = await screen.findByRole("button", { name: "接入当前设备" });
    expect(invoke).not.toHaveBeenCalled();
    await user.click(connect);
    const payload = JSON.parse(
      decodeURIComponent(
        invoke.mock.calls[0][0].slice(
          "wails:event:emit:desktop:device-connect:".length,
        ),
      ),
    );
    expect(payload).toEqual({
      access_token: "employee-token",
      api_path: "/api/devices/connect",
    });
    expect(screen.queryByText("在线")).not.toBeInTheDocument();
    registered = true;
    act(() =>
      window.dispatchEvent(
        new CustomEvent("colink:device-status", {
          detail: {
            type: "status",
            device_id: "device-ada",
            status: "pending",
            online: false,
          },
        }),
      ),
    );
    expect(
      await screen.findByText("当前设备编号：device-ada"),
    ).toBeInTheDocument();
    expect(await screen.findByText("Ada 的工作电脑")).toBeInTheDocument();
    online = true;
    act(() =>
      window.dispatchEvent(
        new CustomEvent("colink:device-status", {
          detail: {
            type: "status",
            device_id: "device-ada",
            status: "approved",
            online: true,
          },
        }),
      ),
    );
    await waitFor(() => expect(screen.getAllByText("在线")).toHaveLength(2));
    act(() =>
      applyRenewedAccessToken(
        new Response(null, {
          headers: { "X-Octop-Access-Token": "renewed-token" },
        }),
      ),
    );
    expect(invoke).toHaveBeenLastCalledWith(
      "wails:event:emit:desktop:device-refresh:%7B%22access_token%22%3A%22renewed-token%22%7D",
    );
    expect(
      invoke.mock.calls.filter(([message]) =>
        message.includes("device-connect:"),
      ),
    ).toHaveLength(1);
    act(() =>
      window.dispatchEvent(
        new CustomEvent("colink:device-status", {
          detail: {
            type: "error",
            code: "connect_failed",
            message: "本机连接已中止",
          },
        }),
      ),
    );
    expect(
      await screen.findByText("connect_failed: 本机连接已中止"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "断开当前设备" }));
    expect(invoke).toHaveBeenLastCalledWith(
      "wails:event:emit:desktop:device-disconnect",
    );
    act(() =>
      window.dispatchEvent(
        new CustomEvent("colink:device-status", {
          detail: { type: "disconnected" },
        }),
      ),
    );
    expect(await screen.findByText("当前设备已断开")).toBeInTheDocument();
    invoke.mockClear();
    act(() =>
      applyRenewedAccessToken(
        new Response(null, {
          headers: { "X-Octop-Access-Token": "next-token" },
        }),
      ),
    );
    expect(invoke).not.toHaveBeenCalled();
  });

  it("离开设备页保持连接，切换账号和退出认证树会主动断开且不自动恢复", async () => {
    const invoke = vi.fn();
    (window as DesktopWindow)._wails = { invoke };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/setup/status") return json({ setup_required: false });
        if (url === "/api/auth/me") return json(employee);
        if (url === "/api/settings/timezone")
          return json({ timezone: "Asia/Shanghai" });
        if (url === "/api/devices") return json([]);
        throw new Error(`Unexpected HTTP request: ${url}`);
      }),
    );
    function SessionControls() {
      const [onDevices, setOnDevices] = useState(true);
      const setUser = useSetCurrentUser();
      return (
        <>
          <button onClick={() => setOnDevices(false)}>返回对话</button>
          <button
            onClick={() => setUser({ ...employee, id: 3, username: "bob" })}
          >
            切换账号
          </button>
          {onDevices ? (
            <Suspense fallback={null}>
              {routeConfigs.find((route) => route.path === "/devices")?.element}
            </Suspense>
          ) : (
            <div>对话页</div>
          )}
        </>
      );
    }
    const view = render(
      <MemoryRouter>
        <AuthGuard>
          <SessionControls />
        </AuthGuard>
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "接入当前设备" }),
    );
    const connectCall = invoke.mock.calls[0]?.[0];
    await user.click(screen.getByRole("button", { name: "返回对话" }));
    expect(screen.getByText("对话页")).toBeInTheDocument();
    expect(invoke.mock.calls).toEqual([[connectCall]]);
    await user.click(screen.getByRole("button", { name: "切换账号" }));
    await waitFor(() =>
      expect(invoke).toHaveBeenLastCalledWith(
        "wails:event:emit:desktop:device-disconnect",
      ),
    );
    act(() =>
      window.dispatchEvent(
        new CustomEvent("colink:device-status", {
          detail: {
            type: "status",
            device_id: "old-account-device",
            status: "approved",
            online: true,
          },
        }),
      ),
    );
    expect(screen.queryByText(/old-account-device/)).not.toBeInTheDocument();
    expect(
      invoke.mock.calls.filter(([message]) =>
        message.includes("device-connect:"),
      ),
    ).toHaveLength(1);
    invoke.mockClear();
    act(() => window.dispatchEvent(new Event("pagehide")));
    expect(invoke).toHaveBeenCalledWith(
      "wails:event:emit:desktop:device-disconnect",
    );
    invoke.mockClear();
    view.unmount();
    expect(invoke).toHaveBeenCalledWith(
      "wails:event:emit:desktop:device-disconnect",
    );
  });
});
