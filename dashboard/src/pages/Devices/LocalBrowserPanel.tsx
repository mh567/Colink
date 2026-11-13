import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Alert, Button, Card, Input, Space, Typography, theme } from "antd";
import { useTranslation } from "react-i18next";

type BrowserAction =
  | "open"
  | "list"
  | "new_tab"
  | "select_tab"
  | "close_tab"
  | "query"
  | "capture"
  | "close";

interface BrowserTab {
  id: string;
  title: string;
  url: string;
}

interface BrowserReply {
  request_id: string;
  device_id: string;
  tabs?: BrowserTab[];
  tab_id?: string;
  text?: string;
  title?: string;
  url?: string;
  image?: string;
  incomplete?: boolean;
  error?: string;
}

interface BrowserRequest {
  id: string;
  action: BrowserAction;
  tabId?: string;
  createsTab: boolean;
  openUrlAfterLaunch?: string;
  timeout: number;
}

type BridgeState = "connecting" | "ready" | "unavailable";

interface BrowserReady {
  request_id: string;
  device_id: string;
  capability: string;
  operation_deadline?: boolean;
}

const STATUS_EVENT = "colink:browser-status";
const READY_EVENT = "colink:browser-ready";

function requestId() {
  return Array.from(window.crypto.getRandomValues(new Uint8Array(16)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

function invokeDesktop(message: string) {
  const invoke = (
    window as Window & { _wails?: { invoke?: (message: string) => void } }
  )._wails?.invoke;
  if (!invoke) throw new Error("Desktop client unavailable");
  invoke(message);
}

function pageAddress(address: string) {
  if (!/^https?:\/\//i.test(address) || address.includes("\\")) return false;
  try {
    const url = new URL(address);
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      !url.username &&
      !url.password &&
      Boolean(url.hostname)
    );
  } catch {
    return false;
  }
}

function displayAddress(address = "") {
  return address.split(/[?#]/, 1)[0];
}

function displayTitle(title = "") {
  return title.replace(/\bhttps?:\/\/[^\s<>"]+/gi, displayAddress);
}

export default function LocalBrowserPanel({ deviceId }: { deviceId: string }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const id = useId();
  const request = useRef<BrowserRequest | null>(null);
  const hello = useRef<{ id: string; timeout: number } | null>(null);
  const bridgeProof = useRef<Pick<
    BrowserReady,
    "capability" | "operation_deadline"
  > | null>(null);
  const tabNumbers = useRef(new Map<string, number>());
  const [bridge, setBridge] = useState<BridgeState>("connecting");
  const [busy, setBusy] = useState<BrowserAction | null>(null);
  const [opened, setOpened] = useState(false);
  const [address, setAddress] = useState("");
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [selected, setSelected] = useState("");
  const [focusedTab, setFocusedTab] = useState("");
  const [instruction, setInstruction] = useState("");
  const [result, setResult] = useState<BrowserReply | null>(null);
  const [capture, setCapture] = useState<BrowserReply | null>(null);
  const [error, setError] = useState("");

  const connect = useCallback(() => {
    if (hello.current) return;
    bridgeProof.current = null;
    setBridge("connecting");
    setError("");
    const id = requestId();
    const timeout = window.setTimeout(() => {
      hello.current = null;
      setBridge("unavailable");
    }, 8_000);
    hello.current = { id, timeout };
    try {
      invokeDesktop(`colink:browser-hello:${id}`);
    } catch {
      window.clearTimeout(timeout);
      hello.current = null;
      setBridge("unavailable");
    }
  }, []);

  useEffect(() => {
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<BrowserReady>).detail;
      const pending = hello.current;
      if (
        !pending ||
        detail?.request_id !== pending.id ||
        detail.device_id !== deviceId ||
        typeof detail.capability !== "string" ||
        !/^[0-9a-f]{64}$/.test(detail.capability)
      )
        return;
      window.clearTimeout(pending.timeout);
      hello.current = null;
      bridgeProof.current = detail;
      setBridge("ready");
    };
    window.addEventListener(READY_EVENT, receive);
    connect();
    return () => {
      window.removeEventListener(READY_EVENT, receive);
      if (hello.current) window.clearTimeout(hello.current.timeout);
      hello.current = null;
      bridgeProof.current = null;
    };
  }, [connect, deviceId]);

  const send = useCallback(
    (
      action: BrowserAction,
      payload: Record<string, string> = {},
      openUrlAfterLaunch?: string,
    ) => {
      const proof = bridgeProof.current;
      if (!proof || request.current) return;
      const id = requestId();
      const timeout = window.setTimeout(
        () => {
          request.current = null;
          bridgeProof.current = null;
          setBridge("unavailable");
          setBusy(null);
          setFocusedTab("");
          setError("BROWSER_NO_RESPONSE");
        },
        action === "query" ? 180_000 : 120_000,
      );
      request.current = {
        id,
        action,
        tabId: payload.tab_id,
        createsTab:
          action === "new_tab" || (action === "open" && !!payload.url),
        openUrlAfterLaunch,
        timeout,
      };
      setError("");
      setBusy(action);
      if (action === "list" || action === "select_tab") setFocusedTab("");
      if (action === "query") setResult(null);
      if (action === "capture") setCapture(null);
      try {
        invokeDesktop(
          `colink:browser:${encodeURIComponent(
            JSON.stringify({
              request_id: id,
              action,
              ...payload,
              capability: proof.capability,
              ...(proof.operation_deadline === true
                ? {
                    deadline_ms:
                      Date.now() + (action === "query" ? 165_000 : 110_000),
                  }
                : {}),
            }),
          )}`,
        );
      } catch {
        window.clearTimeout(timeout);
        request.current = null;
        bridgeProof.current = null;
        setBridge("unavailable");
        setBusy(null);
      }
    },
    [],
  );

  useEffect(() => {
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<BrowserReply>).detail;
      const pending = request.current;
      if (
        !pending ||
        detail?.device_id !== deviceId ||
        detail.request_id !== pending.id
      )
        return;
      window.clearTimeout(pending.timeout);
      request.current = null;
      setBusy(null);
      const resetBrowser = () => {
        tabNumbers.current.clear();
        setOpened(false);
        setTabs([]);
        setSelected("");
        setFocusedTab("");
        setResult(null);
        setCapture(null);
      };
      if (detail.error) {
        setError(detail.error);
        if (
          detail.error === "BROWSER_NOT_OPEN" ||
          detail.error === "BROWSER_NOT_AUTHORIZED"
        ) {
          resetBrowser();
        } else if (
          detail.error === "BROWSER_READ_BLOCKED" ||
          detail.error === "BROWSER_TAB_NOT_FOUND"
        ) {
          setFocusedTab("");
        }
        return;
      }

      if (pending.action === "close") {
        resetBrowser();
        return;
      }
      setOpened(true);
      if (detail.tabs || pending.createsTab) {
        const nextTabs = (detail.tabs ?? []).map((tab) => {
          const numbers = tabNumbers.current;
          if (!numbers.has(tab.id)) numbers.set(tab.id, numbers.size + 1);
          return {
            ...tab,
            title: displayTitle(tab.title),
            url: displayAddress(tab.url),
          };
        });
        const hasTab = (id?: string) => nextTabs.some((tab) => tab.id === id);
        const focused =
          pending.action === "select_tab" && hasTab(pending.tabId)
            ? pending.tabId
            : "";
        setFocusedTab(focused ?? "");
        setTabs(nextTabs);
        setSelected((previous) => {
          if (pending.createsTab) {
            return hasTab(detail.tab_id) ? detail.tab_id ?? "" : "";
          }
          if (focused) return focused;
          if (hasTab(previous)) return previous;
          return nextTabs.length === 1 ? nextTabs[0].id : "";
        });
      }
      if (pending.openUrlAfterLaunch) {
        send("new_tab", { url: pending.openUrlAfterLaunch });
      }
      if (pending.action === "query") setResult(detail);
      if (pending.action === "capture") {
        if (detail.image?.startsWith("iVBORw0KGgo")) setCapture(detail);
        else setError("BROWSER_TASK_FAILED");
      }
    };
    window.addEventListener(STATUS_EVENT, receive);
    return () => window.removeEventListener(STATUS_EVENT, receive);
  }, [deviceId, send]);

  useEffect(
    () => () => {
      if (request.current) window.clearTimeout(request.current.timeout);
    },
    [],
  );

  const tabOptions = tabs.map((tab) => ({
    ...tab,
    label:
      tab.title && tab.url && tab.title !== tab.url
        ? `${tab.title} (${tab.url})`
        : tab.title || tab.url || t("localBrowser.untitled", "空白标签页"),
  }));
  const labelCounts = new Map<string, number>();
  for (const { label } of tabOptions) {
    labelCounts.set(label, (labelCounts.get(label) ?? 0) + 1);
  }
  const selectedTab = tabOptions.find((tab) => tab.id === selected);
  const readableTab = selectedTab && pageAddress(selectedTab.url);
  const ambiguous = Boolean(
    readableTab && (labelCounts.get(selectedTab.label) ?? 0) > 1,
  );
  const needsFocus = ambiguous && focusedTab !== selected;
  const canRead = readableTab && !needsFocus;
  const blocked = bridge !== "ready" || busy !== null;
  const open = (action: "open" | "new_tab") => {
    const url = address.trim();
    if (url && !pageAddress(url)) {
      setError("BROWSER_INVALID_URL");
      return;
    }
    if (
      action === "open" &&
      url &&
      bridgeProof.current?.operation_deadline !== true
    ) {
      // Older clients check new_tab permission only on a separate request.
      send("open", {}, url);
      return;
    }
    send(action, url ? { url } : {});
  };
  const read = (action: "query" | "capture") => {
    if (!selectedTab || !canRead) return;
    const text = instruction.trim();
    if (action === "query" && !text) return;
    send(action, {
      tab_id: selectedTab.id,
      url: selectedTab.url,
      ...(action === "query" ? { instruction: text } : {}),
    });
  };
  const title = t("localBrowser.title", "本机浏览器");
  const errors: Record<string, string> = {
    BROWSER_INVALID_URL: t(
      "localBrowser.errors.BROWSER_INVALID_URL",
      "请输入有效的 HTTP 或 HTTPS 网页地址。",
    ),
    BROWSER_NOT_INSTALLED: t(
      "localBrowser.errors.BROWSER_NOT_INSTALLED",
      "未找到受支持的浏览器，请联系管理员部署浏览器。",
    ),
    BROWSER_NOT_AUTHORIZED: t(
      "localBrowser.errors.BROWSER_NOT_AUTHORIZED",
      "请确认当前设备在线，并联系管理员核对本机浏览器权限。",
    ),
    BROWSER_TIMEOUT: t(
      "localBrowser.errors.BROWSER_TIMEOUT",
      "浏览器操作超时，请重试。",
    ),
    BROWSER_NO_RESPONSE: t(
      "localBrowser.errors.BROWSER_NO_RESPONSE",
      "未收到桌面客户端回执，请重新连接后重试。",
    ),
    BROWSER_SERVICE_UNAVAILABLE: t(
      "localBrowser.errors.BROWSER_SERVICE_UNAVAILABLE",
      "浏览器服务暂不可用，请稍后重试。",
    ),
  };
  const resultTitle = displayTitle(result?.title);
  const resultUrl = displayAddress(result?.url);
  const captureSource =
    displayTitle(capture?.title) || displayAddress(capture?.url);

  return (
    <Card>
      <section aria-labelledby={`${id}-title`}>
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Typography.Title level={5} id={`${id}-title`} style={{ margin: 0 }}>
            {title}
          </Typography.Title>
          <Typography.Text type="secondary">
            {t(
              "localBrowser.hint",
              "请在专用浏览器中自行登录并打开查询页面。返回后刷新标签页，再选择要读取的页面。",
            )}
          </Typography.Text>

          {bridge === "connecting" && (
            <Typography.Text role="status" type="secondary">
              {t("localBrowser.connecting", "正在连接桌面客户端…")}
            </Typography.Text>
          )}
          {bridge === "unavailable" && (
            <div>
              <Button onClick={connect}>
                {t("localBrowser.reconnect", "重试连接桌面客户端")}
              </Button>
            </div>
          )}
          <label htmlFor={`${id}-address`}>
            {t("localBrowser.address", "网页地址（可选）")}
          </label>
          <Input
            id={`${id}-address`}
            value={address}
            maxLength={4096}
            onChange={(event) => setAddress(event.target.value)}
          />
          <Space wrap>
            <Button
              type="primary"
              loading={busy === "open"}
              disabled={blocked}
              onClick={() => open("open")}
            >
              {t("localBrowser.open", "打开专用浏览器")}
            </Button>
            <Button
              loading={busy === "list"}
              disabled={blocked}
              onClick={() => send("list")}
            >
              {t("localBrowser.list", "刷新标签页")}
            </Button>
            <Button
              loading={busy === "new_tab"}
              disabled={!opened || blocked}
              onClick={() => open("new_tab")}
            >
              {t("localBrowser.newTab", "新开标签页")}
            </Button>
            <Button
              loading={busy === "close"}
              disabled={!opened || blocked}
              onClick={() => send("close")}
            >
              {t("localBrowser.close", "关闭专用浏览器")}
            </Button>
          </Space>

          {opened && (
            <>
              <label htmlFor={`${id}-tabs`}>
                {t("localBrowser.tabs", "标签页")}
              </label>
              <select
                id={`${id}-tabs`}
                value={selected}
                disabled={!tabs.length || blocked}
                onChange={(event) =>
                  send("select_tab", { tab_id: event.target.value })
                }
                style={{
                  width: "100%",
                  border: `1px solid ${token.colorBorder}`,
                  borderRadius: token.borderRadius,
                  background: token.colorBgContainer,
                  color: token.colorText,
                  font: "inherit",
                  padding: "7px 11px",
                }}
              >
                {!selected && (
                  <option value="" disabled>
                    {tabs.length
                      ? t("localBrowser.selectTab", "选择标签页")
                      : t("localBrowser.noTabs", "没有可用标签页")}
                  </option>
                )}
                {tabOptions.map((tab) => (
                  <option key={tab.id} value={tab.id}>
                    {(labelCounts.get(tab.label) ?? 0) > 1
                      ? t(
                          "localBrowser.numberedTab",
                          "{{page}}（标签页 {{number}}）",
                          {
                            page: tab.label,
                            number: tabNumbers.current.get(tab.id),
                          },
                        )
                      : tab.label}
                  </option>
                ))}
              </select>
              {selectedTab?.url && (
                <Typography.Text style={{ overflowWrap: "anywhere" }}>
                  {selectedTab.url}
                </Typography.Text>
              )}
              {ambiguous && (
                <Typography.Text type="secondary" role="status">
                  {needsFocus
                    ? t(
                        "localBrowser.focusRequired",
                        "这些标签页的标题和显示地址相同，请先在浏览器中定位选中的标签页。",
                      )
                    : t(
                        "localBrowser.checkFocusedTab",
                        "请在专用浏览器中核对当前标签页，再查询或截图。",
                      )}
                </Typography.Text>
              )}
              <Space wrap>
                {ambiguous && (
                  <Button
                    loading={busy === "select_tab"}
                    disabled={blocked}
                    onClick={() => send("select_tab", { tab_id: selected })}
                  >
                    {t("localBrowser.focusTab", "在浏览器中定位标签页")}
                  </Button>
                )}
                <Button
                  loading={busy === "close_tab"}
                  disabled={!selectedTab || blocked}
                  onClick={() =>
                    selectedTab && send("close_tab", { tab_id: selectedTab.id })
                  }
                >
                  {t("localBrowser.closeTab", "关闭选中标签页")}
                </Button>
                <Button
                  loading={busy === "capture"}
                  disabled={!canRead || blocked}
                  onClick={() => read("capture")}
                >
                  {t("localBrowser.capture", "截取当前页")}
                </Button>
              </Space>
              <label htmlFor={`${id}-question`}>
                {t("localBrowser.question", "查询问题")}
              </label>
              <Typography.Text type="secondary">
                {t(
                  "localBrowser.queryHint",
                  "查询会将当前页的可见文字发送给企业模型。",
                )}
              </Typography.Text>
              <Input.TextArea
                id={`${id}-question`}
                value={instruction}
                maxLength={8000}
                autoSize={{ minRows: 2, maxRows: 5 }}
                onChange={(event) => setInstruction(event.target.value)}
              />
              <div>
                <Button
                  type="primary"
                  loading={busy === "query"}
                  disabled={!canRead || !instruction.trim() || blocked}
                  onClick={() => read("query")}
                >
                  {t("localBrowser.query", "查询当前页")}
                </Button>
              </div>
            </>
          )}
          {error && (
            <Alert
              showIcon
              type="error"
              message={
                errors[error] ??
                t(
                  `localBrowser.errors.${error}`,
                  t(
                    "localBrowser.errors.BROWSER_TASK_FAILED",
                    "无法完成浏览器操作，请重试。",
                  ),
                )
              }
            />
          )}
          {result?.text && (
            <section aria-label={t("localBrowser.result", "查询结果")}>
              <Typography.Title level={5}>
                {t("localBrowser.result", "查询结果")}
              </Typography.Title>
              {result.incomplete === true && (
                <Alert
                  type="info"
                  showIcon
                  message={t(
                    "localBrowser.incompleteResult",
                    "此结果仅基于部分页面内容。",
                  )}
                />
              )}
              <Typography.Paragraph
                style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
              >
                {result.text}
              </Typography.Paragraph>
              {(resultTitle || resultUrl) && (
                <Space direction="vertical" size={2}>
                  <Typography.Text strong>
                    {t("localBrowser.source", "来源")}
                  </Typography.Text>
                  {resultTitle && (
                    <Typography.Text>{resultTitle}</Typography.Text>
                  )}
                  {resultUrl && (
                    <Typography.Text style={{ overflowWrap: "anywhere" }}>
                      {resultUrl}
                    </Typography.Text>
                  )}
                </Space>
              )}
            </section>
          )}
          {capture?.image && (
            <figure style={{ margin: 0 }}>
              <img
                src={`data:image/png;base64,${capture.image}`}
                alt={t("localBrowser.screenshot", "当前页截图")}
                style={{
                  display: "block",
                  maxWidth: "100%",
                  borderRadius: token.borderRadius,
                }}
              />
              {captureSource && (
                <Typography.Text
                  type="secondary"
                  style={{ overflowWrap: "anywhere" }}
                >
                  {captureSource}
                </Typography.Text>
              )}
            </figure>
          )}
        </Space>
      </section>
    </Card>
  );
}
