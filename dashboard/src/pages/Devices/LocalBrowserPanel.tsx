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
  text?: string;
  title?: string;
  url?: string;
  image?: string;
  error?: string;
}

interface BrowserRequest {
  id: string;
  action: BrowserAction;
  tabId?: string;
  timeout: number;
}

type BridgeState = "connecting" | "ready" | "unavailable";

interface BrowserReady {
  request_id: string;
  device_id: string;
  capability: string;
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

export default function LocalBrowserPanel({ deviceId }: { deviceId: string }) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const id = useId();
  const request = useRef<BrowserRequest | null>(null);
  const hello = useRef<{ id: string; timeout: number } | null>(null);
  const capability = useRef("");
  const [bridge, setBridge] = useState<BridgeState>("connecting");
  const [busy, setBusy] = useState<BrowserAction | null>(null);
  const [opened, setOpened] = useState(false);
  const [address, setAddress] = useState("");
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [selected, setSelected] = useState("");
  const [instruction, setInstruction] = useState("");
  const [result, setResult] = useState<BrowserReply | null>(null);
  const [capture, setCapture] = useState<BrowserReply | null>(null);
  const [error, setError] = useState("");

  const connect = useCallback(() => {
    if (hello.current) return;
    capability.current = "";
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
      capability.current = detail.capability;
      setBridge("ready");
    };
    window.addEventListener(READY_EVENT, receive);
    connect();
    return () => {
      window.removeEventListener(READY_EVENT, receive);
      if (hello.current) window.clearTimeout(hello.current.timeout);
      hello.current = null;
      capability.current = "";
    };
  }, [connect, deviceId]);

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
      if (detail.error) {
        setError(detail.error);
        if (
          detail.error === "BROWSER_NOT_OPEN" ||
          detail.error === "BROWSER_NOT_AUTHORIZED"
        ) {
          setOpened(false);
          setTabs([]);
          setSelected("");
          setResult(null);
          setCapture(null);
        }
        return;
      }

      if (pending.action === "close") {
        setOpened(false);
        setTabs([]);
        setSelected("");
        setResult(null);
        setCapture(null);
        return;
      }
      setOpened(true);
      if (detail.tabs) {
        const nextTabs = detail.tabs;
        const added = nextTabs.find(
          (next) =>
            (pending.action === "new_tab" ||
              (pending.action === "open" && pageAddress(next.url))) &&
            tabs.every((tab) => tab.id !== next.id),
        );
        setTabs(nextTabs);
        setSelected((previous) => {
          const next =
            pending.action === "select_tab" ? pending.tabId : previous;
          return (
            added?.id ??
            nextTabs.find((tab) => tab.id === next)?.id ??
            nextTabs[0]?.id ??
            ""
          );
        });
      }
      if (pending.action === "query") setResult(detail);
      if (pending.action === "capture") {
        if (detail.image?.startsWith("iVBORw0KGgo")) setCapture(detail);
        else setError("BROWSER_TASK_FAILED");
      }
    };
    window.addEventListener(STATUS_EVENT, receive);
    return () => window.removeEventListener(STATUS_EVENT, receive);
  }, [deviceId, tabs]);

  useEffect(
    () => () => {
      if (request.current) window.clearTimeout(request.current.timeout);
    },
    [],
  );

  const selectedTab = tabs.find((tab) => tab.id === selected);
  const canRead = Boolean(selectedTab && pageAddress(selectedTab.url));
  const blocked = bridge !== "ready" || busy !== null;
  const send = (
    action: BrowserAction,
    payload: Record<string, string> = {},
  ) => {
    const proof = capability.current;
    if (!proof || request.current) return;
    const id = requestId();
    const timeout = window.setTimeout(
      () => {
        request.current = null;
        capability.current = "";
        setBridge("unavailable");
        setBusy(null);
        setError("BROWSER_TIMEOUT");
      },
      action === "query" ? 180_000 : 120_000,
    );
    request.current = { id, action, tabId: payload.tab_id, timeout };
    setError("");
    setBusy(action);
    if (action === "query") setResult(null);
    if (action === "capture") setCapture(null);
    try {
      invokeDesktop(
        `colink:browser:${encodeURIComponent(
          JSON.stringify({
            request_id: id,
            action,
            ...payload,
            capability: proof,
          }),
        )}`,
      );
    } catch {
      window.clearTimeout(timeout);
      request.current = null;
      capability.current = "";
      setBridge("unavailable");
      setBusy(null);
    }
  };

  const open = (action: "open" | "new_tab") => {
    const url = address.trim();
    if (url && !pageAddress(url)) {
      setError("BROWSER_INVALID_URL");
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
  };

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
                {!tabs.length && (
                  <option value="">
                    {t("localBrowser.noTabs", "没有可用标签页")}
                  </option>
                )}
                {tabs.map((tab) => (
                  <option key={tab.id} value={tab.id}>
                    {tab.title ||
                      tab.url ||
                      t("localBrowser.untitled", "空白标签页")}
                  </option>
                ))}
              </select>
              {selectedTab?.url && (
                <Typography.Text style={{ overflowWrap: "anywhere" }}>
                  {selectedTab.url}
                </Typography.Text>
              )}
              <Space wrap>
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
              <Typography.Paragraph
                style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
              >
                {result.text}
              </Typography.Paragraph>
              {(result.title || result.url) && (
                <Space direction="vertical" size={2}>
                  <Typography.Text strong>
                    {t("localBrowser.source", "来源")}
                  </Typography.Text>
                  {result.title && (
                    <Typography.Text>{result.title}</Typography.Text>
                  )}
                  {result.url && (
                    <Typography.Text style={{ overflowWrap: "anywhere" }}>
                      {result.url}
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
              {(capture.title || capture.url) && (
                <Typography.Text
                  type="secondary"
                  style={{ overflowWrap: "anywhere" }}
                >
                  {capture.title || capture.url}
                </Typography.Text>
              )}
            </figure>
          )}
        </Space>
      </section>
    </Card>
  );
}
