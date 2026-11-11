import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OctopUser } from "../../api/modules/auth";
import { DesktopDeviceProvider } from "../../context/DesktopDeviceContext";
import { CurrentUserProvider } from "../../hooks/useCurrentUser";
import DevicesPage from "./index";

const employee: OctopUser = {
  id: 42,
  username: "ada",
  role: "user",
  display_name: "Ada",
  locale: "zh",
  permissions: ["local_browser"],
};

const prefix = "colink:browser:";
const helloPrefix = "colink:browser-hello:";
const capability = "a".repeat(64);
const tab = {
  id: "cbd6ae4caf1a426ab1b0f67824f06688",
  title: "示例库存",
  url: "https://intranet.example/inventory",
};
const blankTab = {
  id: "72fa6899417c4d78a22666e74f86e067",
  title: "",
  url: "",
};

function startPage(user = employee) {
  const invoke = vi.fn<(message: string) => void>();
  const desktop = window as Window & { _wails?: { invoke: typeof invoke } };
  desktop._wails = { invoke };
  vi.stubGlobal("BASE_URL", "");
  const getStyle = window.getComputedStyle.bind(window);
  vi.stubGlobal("getComputedStyle", (element: Element) => getStyle(element));
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            String(url).endsWith("/settings/timezone")
              ? { timezone: "Asia/Shanghai" }
              : [],
          ),
          { headers: { "Content-Type": "application/json" } },
        ),
    ),
  );
  render(
    <CurrentUserProvider user={user} setUser={vi.fn()}>
      <DesktopDeviceProvider>
        <DevicesPage />
      </DesktopDeviceProvider>
    </CurrentUserProvider>,
  );
  return { invoke, user: userEvent.setup() };
}

async function emit(name: string, detail: object) {
  await act(async () => {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  });
}

function connectDevice(
  status: "pending" | "approved",
  online: boolean,
  deviceId = "desktop-ada",
) {
  return emit("colink:device-status", {
    type: "status",
    device_id: deviceId,
    status,
    online,
  });
}

function helloRequest(invoke: ReturnType<typeof startPage>["invoke"]) {
  const message = invoke.mock.calls
    .map(([call]) => call)
    .filter((call) => call.startsWith(helloPrefix))
    .at(-1);
  expect(message).toMatch(/^colink:browser-hello:[0-9a-f]{32}$/);
  return message!.slice(helloPrefix.length);
}

function approveBrowserBridge(invoke: ReturnType<typeof startPage>["invoke"]) {
  return emit("colink:browser-ready", {
    request_id: helloRequest(invoke),
    device_id: "desktop-ada",
    capability,
  });
}

function lastRequest(invoke: ReturnType<typeof startPage>["invoke"]) {
  const message = invoke.mock.calls
    .map(([call]) => call)
    .filter((call) => call.startsWith(prefix))
    .at(-1);
  expect(message).toBeDefined();
  return JSON.parse(
    decodeURIComponent(message!.slice(prefix.length)),
  ) as Record<string, string>;
}

afterEach(() => {
  delete (window as Window & { _wails?: unknown })._wails;
  vi.unstubAllGlobals();
});

describe("本人设备的本机浏览器任务", () => {
  it("打开已批准设备的浏览器并提交选定页面的只读查询", async () => {
    const { invoke, user } = startPage();
    await user.click(screen.getByRole("button", { name: "接入当前设备" }));
    await connectDevice("pending", false);
    expect(screen.queryByRole("heading", { name: "本机浏览器" })).toBeNull();
    await connectDevice("approved", true);
    await approveBrowserBridge(invoke);
    const panel = within(
      await screen.findByRole("region", { name: "本机浏览器" }),
    );
    await user.click(panel.getByLabelText("网页地址（可选）"));
    await user.paste(tab.url);
    await user.click(panel.getByRole("button", { name: "打开专用浏览器" }));
    const open = lastRequest(invoke);
    expect(open).toMatchObject({ action: "open", url: tab.url, capability });
    await emit("colink:browser-status", {
      request_id: open.request_id,
      device_id: "desktop-ada",
      tabs: [blankTab, tab],
    });

    const instruction = "查询示例商品 DEMO-42 的库存";
    await user.click(panel.getByLabelText("查询问题"));
    await user.paste(instruction);
    await user.click(panel.getByRole("button", { name: "查询当前页" }));
    const query = lastRequest(invoke);
    expect(query).toMatchObject({
      action: "query",
      tab_id: tab.id,
      url: tab.url,
      instruction,
    });
    await emit("colink:browser-status", {
      request_id: query.request_id,
      device_id: "desktop-ada",
      text: "DEMO-42 有 7 件库存。",
      title: tab.title,
      url: tab.url,
    });
    const result = within(panel.getByRole("region", { name: "查询结果" }));
    expect(result.getByText("DEMO-42 有 7 件库存。")).toBeInTheDocument();
    expect(result.getByText(tab.title)).toBeInTheDocument();
    expect(result.getByText(tab.url)).toBeInTheDocument();
  });

  it("只接收当前设备的回执，并在设备变更后清除本机截图", async () => {
    const { invoke, user } = startPage();
    await user.click(screen.getByRole("button", { name: "接入当前设备" }));
    await connectDevice("approved", true);
    await approveBrowserBridge(invoke);
    const panel = within(
      await screen.findByRole("region", { name: "本机浏览器" }),
    );
    await user.click(panel.getByRole("button", { name: "打开专用浏览器" }));
    const open = lastRequest(invoke);
    await emit("colink:browser-status", {
      request_id: open.request_id,
      device_id: "different-device",
      tabs: [tab],
    });
    expect(panel.queryByLabelText("标签页")).not.toBeInTheDocument();

    await emit("colink:browser-status", {
      request_id: open.request_id,
      device_id: "desktop-ada",
      tabs: [tab],
    });
    await user.click(panel.getByRole("button", { name: "截取当前页" }));
    const capture = lastRequest(invoke);
    expect(capture).toMatchObject({
      action: "capture",
      tab_id: tab.id,
      url: tab.url,
    });
    const image =
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X49EAAAAASUVORK5CYII=";
    await emit("colink:browser-status", {
      request_id: capture.request_id,
      device_id: "desktop-ada",
      title: tab.title,
      url: tab.url,
      image,
    });
    expect(panel.getByRole("img", { name: "当前页截图" })).toHaveAttribute(
      "src",
      `data:image/png;base64,${image}`,
    );

    await connectDevice("approved", true, "another-approved-device");
    expect(screen.queryByRole("img", { name: "当前页截图" })).toBeNull();
    await emit("colink:browser-status", {
      request_id: capture.request_id,
      device_id: "desktop-ada",
      image,
    });
    expect(screen.queryByRole("img", { name: "当前页截图" })).toBeNull();
  });

  it("刷新、选择和关闭标签页后可关闭专用浏览器", async () => {
    const { invoke, user } = startPage();
    await user.click(screen.getByRole("button", { name: "接入当前设备" }));
    await connectDevice("approved", true);
    await approveBrowserBridge(invoke);
    const panel = within(
      await screen.findByRole("region", { name: "本机浏览器" }),
    );
    const reply = (tabs?: (typeof tab)[]) =>
      emit("colink:browser-status", {
        request_id: lastRequest(invoke).request_id,
        device_id: "desktop-ada",
        tabs,
      });

    await user.click(panel.getByRole("button", { name: "刷新标签页" }));
    expect(lastRequest(invoke).action).toBe("list");
    await reply([tab]);
    await user.click(panel.getByRole("button", { name: "新开标签页" }));
    expect(lastRequest(invoke).action).toBe("new_tab");
    await reply([tab, blankTab]);
    const tabSelect = panel.getByRole("combobox", { name: "标签页" });
    expect(tabSelect).toHaveValue(blankTab.id);
    expect(panel.getByRole("button", { name: "截取当前页" })).toBeDisabled();

    await user.selectOptions(tabSelect, tab.id);
    expect(lastRequest(invoke)).toMatchObject({
      action: "select_tab",
      tab_id: tab.id,
    });
    await reply([tab, blankTab]);
    expect(tabSelect).toHaveValue(tab.id);
    await user.click(panel.getByRole("button", { name: "关闭选中标签页" }));
    expect(lastRequest(invoke)).toMatchObject({
      action: "close_tab",
      tab_id: tab.id,
    });
    await reply([blankTab]);
    expect(tabSelect).toHaveValue(blankTab.id);

    await user.click(panel.getByRole("button", { name: "关闭专用浏览器" }));
    expect(lastRequest(invoke).action).toBe("close");
    await reply();
    expect(panel.queryByLabelText("标签页")).not.toBeInTheDocument();
  });

  it("没有显式授权的管理员在本人设备上也没有浏览器任务入口", async () => {
    const { invoke, user } = startPage({
      ...employee,
      role: "admin",
      permissions: [],
    });
    await user.click(screen.getByRole("button", { name: "接入当前设备" }));
    await connectDevice("approved", true);
    expect(screen.queryByRole("region", { name: "本机浏览器" })).toBeNull();
    expect(invoke.mock.calls.some(([call]) => call.startsWith(prefix))).toBe(
      false,
    );
  });

  it("拒绝非网页地址并显示浏览器缺失的操作提示", async () => {
    const { invoke, user } = startPage();
    await user.click(screen.getByRole("button", { name: "接入当前设备" }));
    await connectDevice("approved", true);
    await approveBrowserBridge(invoke);
    const panel = within(
      await screen.findByRole("region", { name: "本机浏览器" }),
    );
    const address = panel.getByLabelText("网页地址（可选）");
    const open = panel.getByRole("button", { name: "打开专用浏览器" });
    await user.click(address);
    await user.paste("file:///C:/example.txt");
    await user.click(open);
    expect(panel.getByRole("alert")).toHaveTextContent(
      "请输入有效的 HTTP 或 HTTPS 网页地址",
    );
    expect(invoke.mock.calls.some(([call]) => call.startsWith(prefix))).toBe(
      false,
    );
    await user.clear(address);
    await user.click(open);
    const request = lastRequest(invoke);
    await emit("colink:browser-status", {
      request_id: request.request_id,
      device_id: "desktop-ada",
      error: "BROWSER_NOT_INSTALLED",
    });
    expect(panel.getByRole("alert")).toHaveTextContent(
      "未找到受支持的浏览器，请联系管理员部署浏览器。",
    );
  });

  it("桌面握手完成前禁止发送浏览器操作", async () => {
    const { invoke, user } = startPage();
    await user.click(screen.getByRole("button", { name: "接入当前设备" }));
    await connectDevice("approved", true);
    const panel = within(
      await screen.findByRole("region", { name: "本机浏览器" }),
    );
    const open = panel.getByRole("button", { name: "打开专用浏览器" });
    const refresh = panel.getByRole("button", { name: "刷新标签页" });
    expect(open).toBeDisabled();
    expect(refresh).toBeDisabled();
    await user.click(open);
    expect(invoke.mock.calls.some(([call]) => call.startsWith(prefix))).toBe(
      false,
    );
    await emit("colink:browser-ready", {
      request_id: helloRequest(invoke),
      device_id: "different-device",
      capability,
    });
    expect(open).toBeDisabled();
    await approveBrowserBridge(invoke);
    expect(open).toBeEnabled();
    await user.click(open);
    expect(lastRequest(invoke)).toMatchObject({ action: "open", capability });
  });

  it("桌面握手失败后可以重新连接", async () => {
    const { invoke, user } = startPage();
    let failed = false;
    invoke.mockImplementation((message) => {
      if (message.startsWith(helloPrefix) && !failed) {
        failed = true;
        throw new Error("Desktop bridge temporarily unavailable");
      }
    });
    await user.click(screen.getByRole("button", { name: "接入当前设备" }));
    await connectDevice("approved", true);
    const panel = within(
      await screen.findByRole("region", { name: "本机浏览器" }),
    );
    const first = helloRequest(invoke);
    await user.click(panel.getByRole("button", { name: "重试连接桌面客户端" }));
    expect(helloRequest(invoke)).not.toBe(first);
    await approveBrowserBridge(invoke);
    expect(panel.getByRole("button", { name: "打开专用浏览器" })).toBeEnabled();
  });
});
