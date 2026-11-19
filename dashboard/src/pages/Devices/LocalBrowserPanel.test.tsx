import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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
  operations: {
    id: string;
    status: string;
    created_at: string;
    url: string;
  }[] = [],
) {
  return emit("colink:browser-ready", {
    request_id: helloRequest(invoke),
    device_id: "desktop-ada",
    capability,
    operation_deadline: operationDeadline,
  }).then(async () => {
    if (!operationDeadline) return;
    const operationRequest = lastRequest(invoke);
    expect(operationRequest.action).toBe("operations");
    await emit("colink:browser-status", {
      request_id: operationRequest.request_id,
      device_id: "desktop-ada",
      operations,
    });
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

async function startBrowserPanel(
  operationDeadline?: boolean,
  operations: {
    id: string;
    status: string;
    created_at: string;
    url: string;
  }[] = [],
) {
  const { invoke, user } = startPage();
  await user.click(screen.getByRole("button", { name: "接入当前设备" }));
  await connectDevice("approved", true);
  await approveBrowserBridge(invoke, operationDeadline, operations);
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

async function prepareOrderSuggestion(
  tabs: { id: string; title: string; url: string }[] = [tab],
) {
  const browser = await startBrowserPanel(true);
  await browser.click("打开专用浏览器");
  await browser.reply({ tabs, tab_id: tab.id });
  if (tabs.length > 1) {
    await browser.user.selectOptions(
      browser.panel.getByRole("combobox", { name: "标签页" }),
      tab.id,
    );
    await browser.reply({ tabs });
  }
  await browser.click("读取表单");
  await browser.reply({
    forms: [
      {
        id: "form-1",
        label: "订单更新",
        fields: [
          {
            id: "field-1",
            label: "数量",
            type: "text",
            value: "1",
            options: [],
          },
        ],
        submitters: [
          {
            id: "submit-1",
            label: "保存订单",
            action_url: "https://intranet.example/orders/save",
            method: "post",
            target: "_self",
          },
        ],
      },
    ],
  });
  await browser.user.selectOptions(
    browser.panel.getByRole("combobox", { name: "表单" }),
    "form-1",
  );
  await browser.user.selectOptions(
    browser.panel.getByRole("combobox", { name: "提交动作" }),
    "submit-1",
  );
  await browser.user.click(browser.panel.getByLabelText("填写任务"));
  await browser.user.paste("将数量改成 42");
  await browser.click("生成填写建议");
  await browser.reply({
    form_id: "form-1",
    submit_id: "submit-1",
    snapshot_id: "snapshot-1",
    action_url: "https://intranet.example/orders/save",
    method: "post",
    target: "_self",
    fields: [
      { id: "field-1", label: "数量", type: "text", value: "1", options: [] },
    ],
    values: { "field-1": "42" },
  });
  return browser;
}

afterEach(() => {
  vi.useRealTimers();
  delete (window as Window & { _wails?: unknown })._wails;
  vi.unstubAllGlobals();
});

describe("本人设备的本机浏览器任务", () => {
  it("在员工选择表单和提交动作后才生成建议并预览目标和值", async () => {
    const { invoke, panel, user, click, reply } = await startBrowserPanel(true);
    await click("打开专用浏览器");
    await reply({ tabs: [tab], tab_id: tab.id });
    await click("读取表单");
    expect(lastRequest(invoke)).toMatchObject({
      action: "forms",
      tab_id: tab.id,
      url: tab.url,
    });
    await reply({
      forms: [
        {
          id: "form-1",
          label: "订单更新",
          fields: [
            {
              id: "field-1",
              label: "数量\u202e",
              type: "text",
              value: "1\u202e",
              options: [],
            },
          ],
          submitters: [
            {
              id: "submit-1",
              label: "保存订单",
              action_url: "https://intranet.example/orders/save?token=private",
              method: "post",
              target: "_blank",
            },
            {
              id: "submit-3",
              label: "删除订单",
              action_url: "https://intranet.example/orders/delete",
              method: "post",
              target: "_self",
            },
          ],
        },
        {
          id: "form-2",
          label: "备注表单",
          fields: [],
          submitters: [
            {
              id: "submit-2",
              label: "发送备注",
              action_url: "https://intranet.example/comments",
              method: "post",
              target: "_self",
            },
          ],
        },
      ],
    });

    const form = panel.getByRole("combobox", { name: "表单" });
    expect(
      panel.queryByRole("button", { name: "生成填写建议" }),
    ).not.toBeInTheDocument();
    await user.selectOptions(form, "form-1");
    const submitter = panel.getByRole("combobox", { name: "提交动作" });
    expect(submitter).toHaveValue("");
    expect(panel.getByRole("option", { name: "删除订单" })).toBeInTheDocument();
    expect(
      panel.queryByRole("button", { name: "生成填写建议" }),
    ).not.toBeInTheDocument();
    await user.selectOptions(submitter, "submit-1");
    const suggest = panel.getByRole("button", { name: "生成填写建议" });
    expect(suggest).toBeDisabled();
    await user.click(panel.getByLabelText("填写任务"));
    await user.paste("将数量改成 42");
    expect(suggest).toBeEnabled();
    expect(
      panel.getByText("https://intranet.example/orders/save"),
    ).toBeVisible();
    expect(panel.getByText("POST · _blank")).toBeVisible();
    expect(panel.queryByText(/token=private/)).not.toBeInTheDocument();

    await user.click(suggest);
    const suggestion = lastRequest(invoke);
    expect(suggestion).toMatchObject({
      action: "suggest_form",
      form_id: "form-1",
      submit_id: "submit-1",
      tab_id: tab.id,
      url: tab.url,
      instruction: "将数量改成 42",
    });
    await reply({
      form_id: "form-1",
      submit_id: "submit-1",
      snapshot_id: "snapshot-1",
      form_label: "订单更新",
      submit_label: "保存订单",
      action_url: "https://intranet.example/orders/save",
      method: "post",
      target: "_blank",
      fields: [
        {
          id: "field-1",
          label: "数量\u202e",
          type: "text",
          value: "1\u202e",
          options: [],
        },
      ],
      values: { "field-1": "42\u202e" },
    });
    const preview = within(panel.getByRole("region", { name: "填写建议预览" }));
    expect(preview.getByText("订单更新")).toBeVisible();
    expect(preview.getByText("保存订单")).toBeVisible();
    expect(preview.getByText(/数量\\u202e/)).toBeVisible();
    expect(preview.getByText(/1\\u202e → 42\\u202e/)).toBeVisible();
    expect(
      panel.getByRole("region", { name: "填写建议预览" }).textContent,
    ).not.toContain("\u202e");
    const submit = panel.getByRole("button", { name: "提交表单" });
    expect(submit).toBeEnabled();
    await user.click(submit);
    const submission = lastRequest(invoke);
    expect(submission).toMatchObject({
      action: "submit_form",
      form_id: "form-1",
      submit_id: "submit-1",
      snapshot_id: "snapshot-1",
      values: { "field-1": "42\u202e" },
    });
    expect(submission.request_id).not.toBe(suggestion.request_id);
    await reply({
      operation: {
        id: submission.request_id,
        status: "action_sent",
        created_at: "2026-11-19T00:00:00Z",
        url: "https://intranet.example",
      },
    });
    expect(panel.getByRole("status")).toHaveTextContent(
      "操作已发出，请核对业务页面结果。",
    );
    expect(
      panel.queryByRole("button", { name: "提交表单" }),
    ).not.toBeInTheDocument();
  });

  it("切换标签页后清除旧表单建议", async () => {
    const otherTab = {
      ...tab,
      id: "other-tab",
      url: "https://intranet.example/other",
    };
    const { invoke, panel, user } = await prepareOrderSuggestion([
      tab,
      otherTab,
    ]);

    await user.selectOptions(
      panel.getByRole("combobox", { name: "标签页" }),
      otherTab.id,
    );
    expect(lastRequest(invoke)).toMatchObject({
      action: "select_tab",
      tab_id: otherTab.id,
    });
    expect(
      panel.queryByRole("region", { name: "填写建议预览" }),
    ).not.toBeInTheDocument();
    expect(
      panel.queryByRole("button", { name: "提交表单" }),
    ).not.toBeInTheDocument();
  });

  it("切换设备后清除旧设备的表单建议", async () => {
    await prepareOrderSuggestion();

    await connectDevice("approved", true, "another-approved-device");
    expect(
      screen.queryByRole("region", { name: "填写建议预览" }),
    ).not.toBeInTheDocument();
  });

  it("恢复并显示待核对操作，不提供自动重放", async () => {
    const { panel } = await startBrowserPanel(true, [
      {
        id: "operation-sent",
        status: "action_sent",
        created_at: "2026-11-19T00:00:00Z",
        url: "https://intranet.example",
      },
      {
        id: "operation-unknown",
        status: "outcome_unknown",
        created_at: "2026-11-19T00:01:00Z",
        url: "https://intranet.example",
      },
      {
        id: "operation-interrupted",
        status: "interrupted",
        created_at: "2026-11-19T00:02:00Z",
        url: "https://intranet.example",
      },
    ]);
    const status = panel.getByRole("region", { name: "表单操作状态" });
    const statusContent = within(status);
    expect(
      statusContent.getByText(/操作已发出，请核对业务页面结果/),
    ).toBeVisible();
    expect(
      statusContent.getAllByText(/结果待核对，请先检查业务页面/),
    ).toHaveLength(2);
    expect(
      panel.queryByRole("button", { name: /重试|重新提交|再次提交/ }),
    ).not.toBeInTheDocument();
  });

  it("提交回执超时后重连读取状态，期间不重新提交", async () => {
    const { invoke, panel } = await prepareOrderSuggestion();

    vi.useFakeTimers();
    fireEvent.click(panel.getByRole("button", { name: "提交表单" }));
    const submission = lastRequest(invoke);
    expect(submission.action).toBe("submit_form");
    await act(async () => vi.advanceTimersByTimeAsync(120_000));
    expect(panel.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      panel.getByRole("region", { name: "表单操作状态" }),
    ).toHaveTextContent("未收到提交回执，请先检查业务页面");
    expect(
      panel.queryByRole("button", { name: "提交表单" }),
    ).not.toBeInTheDocument();

    fireEvent.click(panel.getByRole("button", { name: "重试连接桌面客户端" }));
    await approveBrowserBridge(invoke, true, [
      {
        id: submission.request_id,
        status: "outcome_unknown",
        created_at: "2026-11-19T00:00:00Z",
        url: "https://intranet.example",
      },
    ]);
    expect(
      panel.getByRole("region", { name: "表单操作状态" }),
    ).toHaveTextContent("结果待核对，请先检查业务页面。");
    const submissions = invoke.mock.calls
      .map(([message]) => message)
      .filter((message) => message.startsWith(prefix))
      .map((message) =>
        JSON.parse(decodeURIComponent(message.slice(prefix.length))),
      )
      .filter((request) => request.action === "submit_form");
    expect(submissions).toHaveLength(1);
    expect(submissions[0].request_id).toBe(submission.request_id);
  });

  it.each(["BROWSER_TIMEOUT", "BROWSER_NO_RESPONSE"])(
    "桌面返回提交错误 %s 后提示核对并查询状态",
    async (error) => {
      const { invoke, panel, click, reply } = await prepareOrderSuggestion();
      await click("提交表单");
      expect(lastRequest(invoke).action).toBe("submit_form");
      await reply({ error });

      expect(panel.queryByRole("alert")).not.toBeInTheDocument();
      expect(
        panel.getByRole("region", { name: "表单操作状态" }),
      ).toHaveTextContent("未收到提交回执，请先检查业务页面");
      await waitFor(() =>
        expect(lastRequest(invoke).action).toBe("operations"),
      );
    },
  );

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

  it("设备断连后保留本机任务失败原因并禁用操作", async () => {
    const { user } = startPage();
    await user.click(screen.getByRole("button", { name: "接入当前设备" }));
    await connectDevice("approved", true);
    await emit("colink:docx-status", {
      device_id: "desktop-ada",
      busy: true,
      grants: [{ id: "grant", path: "/资料", mode: "rw" }],
      tasks: [
        {
          id: "task-1",
          instruction: "整理",
          source: "/资料/输入.docx",
          output: "/资料/结果.docx",
          status: "running",
        },
      ],
    });
    expect(screen.getByLabelText("本机任务记录")).toBeInTheDocument();

    await emit("colink:device-status", {
      type: "error",
      code: "DEVICE_CLIENT_ERROR",
      message: "connection closed",
    });
    await emit("colink:docx-status", {
      device_id: "desktop-ada",
      busy: false,
      grants: [{ id: "grant", path: "/资料", mode: "rw" }],
      tasks: [
        {
          id: "task-1",
          instruction: "整理",
          source: "/资料/输入.docx",
          output: "/资料/结果.docx",
          status: "failed",
          error: "设备会话已改变，任务已停止",
        },
      ],
    });

    expect(screen.getByLabelText("本机任务记录")).toBeInTheDocument();
    expect(screen.getByText("设备会话已改变，任务已停止")).toBeInTheDocument();
    expect(screen.getByLabelText("文件类型")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "撤销 /资料 的目录授权" }),
    ).toBeDisabled();
  });

  it("遵循目录授权向网页上传附件并显示成功回执", async () => {
    const { invoke, user, panel, click, reply } = await startBrowserPanel();
    await emit("colink:docx-status", {
      device_id: "desktop-ada",
      grants: [{ id: "grant-upload", path: "/data/docs", mode: "ro" }],
    });
    await click("打开专用浏览器");
    await reply({ tabs: [tab], tab_id: tab.id });

    const grantSelect = panel.getByLabelText("选择上传目录授权");
    await user.selectOptions(grantSelect, "grant-upload");
    const fileInput = panel.getByLabelText("上传文件名");
    await user.click(fileInput);
    await user.paste("invoice.pdf");
    const selectorInput = panel.getByLabelText("文件输入选择器（可选）");
    await user.click(selectorInput);
    await user.paste("input#upload-field");

    await click("上传附件到页面");
    const uploadReq = lastRequest(invoke);
    expect(uploadReq).toMatchObject({
      action: "upload",
      tab_id: tab.id,
      url: tab.url,
      grant_id: "grant-upload",
      file: "invoice.pdf",
      selector: "input#upload-field",
    });

    await reply({ file: "invoice.pdf" });
    expect(
      await panel.findByText(
        "已为网页选择附件：invoice.pdf。请在网页上核对提交结果。",
      ),
    ).toBeInTheDocument();
  });

  it("遵循读写授权下载网页产物并在回执后显示保存结果", async () => {
    const { invoke, user, panel, click, reply } = await startBrowserPanel();
    await emit("colink:docx-status", {
      device_id: "desktop-ada",
      grants: [
        { id: "grant-ro", path: "/data/ro", mode: "ro" },
        { id: "grant-rw", path: "/data/rw", mode: "rw" },
      ],
    });
    await click("打开专用浏览器");
    await reply({ tabs: [tab], tab_id: tab.id });

    const grantSelect = panel.getByLabelText("选择下载目录授权");
    await user.selectOptions(grantSelect, "grant-rw");
    const fileInput = panel.getByLabelText("保存文件名");
    await user.click(fileInput);
    await user.paste("report.xlsx");
    const urlInput = panel.getByLabelText("下载链接（可选）");
    await user.click(urlInput);
    await user.paste("https://intranet.example/report.xlsx");
    const selectorInput = panel.getByLabelText("下载链接选择器（可选）");
    await user.click(selectorInput);
    await user.paste("#download-link");
    expect(
      panel.getByRole("button", { name: "下载产物到授权目录" }),
    ).toBeDisabled();
    await user.clear(selectorInput);

    await click("下载产物到授权目录");
    const dlReq = lastRequest(invoke);
    expect(dlReq).toMatchObject({
      action: "download",
      tab_id: tab.id,
      url: tab.url,
      grant_id: "grant-rw",
      file: "report.xlsx",
      download_url: "https://intranet.example/report.xlsx",
    });

    await reply({ file: "report.xlsx", size: 2048 });
    expect(
      await panel.findByText("产物已成功保存：report.xlsx（2048 字节）"),
    ).toBeInTheDocument();
  });

  it("上传或下载遇到异常或取消覆盖时展示清晰错误提示", async () => {
    const { user, panel, click, reply } = await startBrowserPanel();
    await emit("colink:docx-status", {
      device_id: "desktop-ada",
      grants: [{ id: "grant-rw", path: "/data/rw", mode: "rw" }],
    });
    await click("打开专用浏览器");
    await reply({ tabs: [tab], tab_id: tab.id });

    // Upload with missing file error
    await user.selectOptions(
      panel.getByLabelText("选择上传目录授权"),
      "grant-rw",
    );
    await user.click(panel.getByLabelText("上传文件名"));
    await user.paste("missing.pdf");
    await click("上传附件到页面");
    await reply({ error: "BROWSER_FILE_NOT_FOUND" });
    expect(
      await panel.findByText("授权文件不存在或无法读取。"),
    ).toBeInTheDocument();

    // Download with overwrite cancellation
    await user.selectOptions(
      panel.getByLabelText("选择下载目录授权"),
      "grant-rw",
    );
    await user.click(panel.getByLabelText("保存文件名"));
    await user.paste("existing.pdf");
    const link = panel.getByLabelText("下载链接（可选）");
    await user.click(link);
    await user.paste("https://intranet.example/report.xlsx");
    await click("下载产物到授权目录");
    await reply({ error: "file operation cancelled" });
    expect(await panel.findByText("已取消本次操作。")).toBeInTheDocument();
  });
});
