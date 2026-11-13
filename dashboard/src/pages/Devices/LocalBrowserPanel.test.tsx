import { act, fireEvent, render, screen, within } from "@testing-library/react";
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

function approveBrowserBridge(
  invoke: ReturnType<typeof startPage>["invoke"],
  operationDeadline?: boolean,
) {
  return emit("colink:browser-ready", {
    request_id: helloRequest(invoke),
    device_id: "desktop-ada",
    capability,
    operation_deadline: operationDeadline,
  });
}

function lastRequest(invoke: ReturnType<typeof startPage>["invoke"]) {
  const message = invoke.mock.calls
    .map(([call]) => call)
    .filter((call) => call.startsWith(prefix))
    .at(-1);
  expect(message).toBeDefined();
  return JSON.parse(decodeURIComponent(message!.slice(prefix.length))) as {
    request_id: string;
    action: string;
    deadline_ms?: number;
  };
}

async function startBrowserPanel(operationDeadline?: boolean) {
  const { invoke, user } = startPage();
  await user.click(screen.getByRole("button", { name: "接入当前设备" }));
  await connectDevice("approved", true);
  await approveBrowserBridge(invoke, operationDeadline);
  const panel = within(screen.getByRole("region", { name: "本机浏览器" }));
  const click = (name: string) =>
    user.click(panel.getByRole("button", { name }));
  const reply = (detail: object) =>
    emit("colink:browser-status", {
      request_id: lastRequest(invoke).request_id,
      device_id: "desktop-ada",
      ...detail,
    });
  return { invoke, user, panel, click, reply };
}

afterEach(() => {
  vi.useRealTimers();
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
    await approveBrowserBridge(invoke, true);
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
      tab_id: tab.id,
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
    const reply = (tabs?: (typeof tab)[], tabId?: string) =>
      emit("colink:browser-status", {
        request_id: lastRequest(invoke).request_id,
        device_id: "desktop-ada",
        tabs,
        tab_id: tabId,
      });

    await user.click(panel.getByRole("button", { name: "刷新标签页" }));
    expect(lastRequest(invoke).action).toBe("list");
    await reply([tab]);
    await user.click(panel.getByRole("button", { name: "新开标签页" }));
    expect(lastRequest(invoke).action).toBe("new_tab");
    await reply([tab, blankTab], blankTab.id);
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

  it("用原生回执指定的标签页打开地址或新开标签页", async () => {
    const { panel, user, click, reply } = await startBrowserPanel(true);
    const otherTab = { ...tab, id: "other-tab" };
    await user.click(panel.getByLabelText("网页地址（可选）"));
    await user.paste(tab.url);
    await click("打开专用浏览器");
    await reply({ tabs: [otherTab, tab], tab_id: tab.id });
    const select = panel.getByRole("combobox", { name: "标签页" });
    expect(select).toHaveValue(tab.id);

    const anotherTab = { ...tab, id: "another-tab" };
    const newestTab = { ...tab, id: "newest-tab" };
    await click("新开标签页");
    await reply({
      tabs: [anotherTab, newestTab, otherTab, tab],
      tab_id: newestTab.id,
    });
    expect(select).toHaveValue(newestTab.id);
  });

  it.each([undefined, "absent-tab"])(
    "打开地址的回执缺少可选中的目标时等待用户选择（%s）",
    async (tabId) => {
      const { panel, user, click, reply } = await startBrowserPanel(true);
      await user.click(panel.getByLabelText("网页地址（可选）"));
      await user.paste(tab.url);
      await click("打开专用浏览器");
      await reply({ tabs: [tab], tab_id: tabId });
      expect(panel.getByRole("combobox", { name: "标签页" })).toHaveValue("");
      expect(panel.getByRole("button", { name: "截取当前页" })).toBeDisabled();
    },
  );

  it("同名同地址标签页的标识保持稳定，刷新或读取被拒后需重新定位", async () => {
    const { invoke, panel, user, click, reply } = await startBrowserPanel(true);
    const duplicate = {
      ...tab,
      id: "duplicate-tab",
      url: `${tab.url}?private=two#second`,
    };
    const first = { ...tab, url: `${tab.url}?private=one#first` };
    await user.click(panel.getByLabelText("网页地址（可选）"));
    await user.paste(tab.url);
    await click("打开专用浏览器");
    await reply({ tabs: [first, duplicate], tab_id: first.id });
    const optionLabels = () =>
      new Map(
        panel
          .getAllByRole<HTMLOptionElement>("option")
          .map((option) => [option.value, option.textContent]),
      );
    const labels = optionLabels();
    expect(labels.get(first.id)).not.toBe(labels.get(duplicate.id));
    expect(
      screen.getByRole("region", { name: "本机浏览器" }).textContent,
    ).not.toMatch(/private=|#first|#second/);

    await user.click(panel.getByLabelText("查询问题"));
    await user.paste("查询示例库存");
    const query = panel.getByRole("button", { name: "查询当前页" });
    const capture = panel.getByRole("button", { name: "截取当前页" });
    expect(query).toBeDisabled();
    expect(capture).toBeDisabled();
    await click("在浏览器中定位标签页");
    expect(lastRequest(invoke)).toMatchObject({
      action: "select_tab",
      tab_id: first.id,
    });
    expect(query).toBeDisabled();
    await reply({ tabs: [duplicate, first] });
    expect(optionLabels()).toEqual(labels);
    expect(
      panel.getByText("请在专用浏览器中核对当前标签页，再查询或截图。"),
    ).toBeVisible();

    await click("查询当前页");
    expect(lastRequest(invoke)).toMatchObject({
      action: "query",
      tab_id: first.id,
      url: tab.url,
    });
    await reply({ error: "BROWSER_READ_BLOCKED" });
    expect(query).toBeDisabled();
    expect(capture).toBeDisabled();

    const select = panel.getByRole("combobox", { name: "标签页" });
    await user.selectOptions(select, duplicate.id);
    expect(lastRequest(invoke)).toMatchObject({
      action: "select_tab",
      tab_id: duplicate.id,
    });
    await reply({ tabs: [duplicate, first] });
    await click("截取当前页");
    expect(lastRequest(invoke)).toMatchObject({
      action: "capture",
      tab_id: duplicate.id,
      url: tab.url,
    });
    await reply({ image: "iVBORw0KGgo", url: duplicate.url });
    expect(panel.getByRole("img", { name: "当前页截图" })).toBeVisible();
    expect(panel.queryByText(duplicate.url)).not.toBeInTheDocument();

    await click("刷新标签页");
    await reply({ tabs: [first, duplicate] });
    expect(optionLabels()).toEqual(labels);
    expect(query).toBeDisabled();
    expect(capture).toBeDisabled();
  });

  it.each([undefined, false])(
    "兼容旧客户端并分别请求打开浏览器和新开标签页（%s）",
    async (operationDeadline) => {
      const { invoke, panel, user, click, reply } = await startBrowserPanel(
        operationDeadline,
      );
      await user.click(panel.getByLabelText("网页地址（可选）"));
      await user.paste(tab.url);
      await click("打开专用浏览器");
      expect(lastRequest(invoke)).toMatchObject({ action: "open" });
      expect(lastRequest(invoke)).not.toHaveProperty("url");
      expect(lastRequest(invoke)).not.toHaveProperty("deadline_ms");
      await reply({ error: "BROWSER_NOT_AUTHORIZED" });
      expect(lastRequest(invoke).action).toBe("open");

      await click("打开专用浏览器");
      await reply({ tabs: [blankTab] });
      expect(lastRequest(invoke)).toMatchObject({
        action: "new_tab",
        url: tab.url,
      });
      expect(lastRequest(invoke)).not.toHaveProperty("deadline_ms");
      await reply({ tabs: [blankTab, tab] });
      const select = panel.getByRole("combobox", { name: "标签页" });
      expect(select).toHaveValue("");
      expect(panel.getByRole("button", { name: "截取当前页" })).toBeDisabled();
      await user.selectOptions(select, tab.id);
      expect(lastRequest(invoke)).toMatchObject({
        action: "select_tab",
        tab_id: tab.id,
      });
      await reply({ tabs: [blankTab, tab] });
      expect(panel.getByRole("button", { name: "截取当前页" })).toBeEnabled();
    },
  );

  it("发送原生截止时间并在原生超时回执后允许重试", async () => {
    const { invoke, panel, user, click, reply } = await startBrowserPanel(true);
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(1_700_000_000_000);
    await click("打开专用浏览器");
    expect(lastRequest(invoke).deadline_ms).toBe(1_700_000_110_000);
    await reply({ tabs: [tab] });
    await user.click(panel.getByLabelText("查询问题"));
    await user.paste("查询示例库存");
    await click("查询当前页");
    expect(lastRequest(invoke).deadline_ms).toBe(1_700_000_165_000);
    await reply({ error: "BROWSER_TIMEOUT" });
    expect(panel.getByRole("alert")).toHaveTextContent(
      "浏览器操作超时，请重试。",
    );
    expect(panel.getByRole("button", { name: "查询当前页" })).toBeEnabled();
    await click("查询当前页");
    expect(lastRequest(invoke).action).toBe("query");
  });

  it("等待原生回执时保留界面超时宽限", async () => {
    const { panel } = await startBrowserPanel(true);
    vi.useFakeTimers();
    const open = panel.getByRole("button", { name: "打开专用浏览器" });
    fireEvent.click(open);
    await act(async () => vi.advanceTimersByTimeAsync(110_000));
    expect(panel.queryByRole("alert")).not.toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(panel.getByRole("alert")).toHaveTextContent("未收到桌面客户端回执");
    expect(
      panel.getByRole("button", { name: "重试连接桌面客户端" }),
    ).toBeEnabled();
  });

  it("服务暂不可用时保留标签页，重试后显示部分查询结果", async () => {
    const { panel, user, click, reply } = await startBrowserPanel();
    await click("打开专用浏览器");
    await reply({ tabs: [tab] });
    await user.click(panel.getByLabelText("查询问题"));
    await user.paste("查询示例库存");
    await click("查询当前页");
    await reply({ error: "BROWSER_SERVICE_UNAVAILABLE" });
    expect(panel.getByRole("alert")).toHaveTextContent(
      "浏览器服务暂不可用，请稍后重试。",
    );
    expect(panel.getByRole("combobox", { name: "标签页" })).toHaveValue(tab.id);
    expect(panel.getByRole("button", { name: "查询当前页" })).toBeEnabled();
    await click("查询当前页");
    await reply({
      text: "DEMO-42 有 7 件库存。",
      title: tab.title,
      url: `${tab.url}?private=one#first`,
      incomplete: true,
    });
    const result = within(panel.getByRole("region", { name: "查询结果" }));
    expect(result.getByText("DEMO-42 有 7 件库存。")).toBeVisible();
    expect(result.getByText("此结果仅基于部分页面内容。")).toBeVisible();
    expect(result.getByText(tab.url)).toBeVisible();
    expect(result.queryByText(/private=|#first/)).not.toBeInTheDocument();
  });
});
