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
  | "forms"
  | "suggest_form"
  | "submit_form"
  | "operations"
  | "upload"
  | "download"
  | "close";

type BrowserValue = string | boolean;

interface BrowserOption {
  label: string;
  value: string;
}

interface BrowserFormField {
  id: string;
  label: string;
  type: string;
  value: BrowserValue;
  options: BrowserOption[];
}

interface BrowserSubmitter {
  id: string;
  label: string;
  action_url: string;
  method: string;
  target: string;
}

interface BrowserForm {
  id: string;
  label: string;
  fields: BrowserFormField[];
  submitters: BrowserSubmitter[];
}

interface BrowserOperation {
  id: string;
  status: string;
  created_at: string;
  url: string;
}

interface BrowserTab {
  id: string;
  title: string;
  url: string;
}

interface DirectoryGrant {
  id: string;
  path: string;
  mode: "ro" | "rw";
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
  file?: string;
  size?: number;
  incomplete?: boolean;
  error?: string;
  forms?: BrowserForm[];
  form_id?: string;
  submit_id?: string;
  snapshot_id?: string;
  form_label?: string;
  submit_label?: string;
  action_url?: string;
  method?: string;
  target?: string;
  fields?: BrowserFormField[];
  values?: Record<string, BrowserValue>;
  operation?: BrowserOperation;
  operations?: BrowserOperation[];
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

function formValue(
  value: BrowserValue,
  t: (key: string, fallback: string) => string,
) {
  if (typeof value === "boolean") {
    return value
      ? t("localBrowser.checked", "已勾选")
      : t("localBrowser.unchecked", "未勾选");
  }
  return displayFormText(value);
}

function operationLabel(
  status: string,
  t: (key: string, fallback: string) => string,
) {
  if (status === "action_sent") {
    return t("localBrowser.operationSent", "操作已发出，请核对业务页面结果。");
  }
  if (status === "outcome_unknown" || status === "interrupted") {
    return t("localBrowser.operationUnknown", "结果待核对，请先检查业务页面。");
  }
  if (status === "stale") {
    return t("localBrowser.operationStale", "页面内容已变化，请重新读取表单。");
  }
  if (status === "cancelled") {
    return t("localBrowser.operationCancelled", "提交已取消。");
  }
  return "";
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

function displayFormText(value: string) {
  return Array.from(value, (character) => {
    const code = character.codePointAt(0) ?? 0;
    if (code === 92) return "\\\\";
    if (
      code <= 31 ||
      (code >= 127 && code <= 159) ||
      code === 8206 ||
      code === 8207 ||
      (code >= 8232 && code <= 8238) ||
      (code >= 8294 && code <= 8297)
    ) {
      return `\\u${code.toString(16).padStart(4, "0")}`;
    }
    return character;
  }).join("");
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
  const [forms, setForms] = useState<BrowserForm[]>([]);
  const [formId, setFormId] = useState("");
  const [submitId, setSubmitId] = useState("");
  const [formInstruction, setFormInstruction] = useState("");
  const [suggestion, setSuggestion] = useState<BrowserReply | null>(null);
  const [operations, setOperations] = useState<BrowserOperation[]>([]);
  const [submissionUncertain, setSubmissionUncertain] = useState(false);
  const [uncertainOperationId, setUncertainOperationId] = useState("");
  const [error, setError] = useState("");
  const [grants, setGrants] = useState<DirectoryGrant[]>([]);
  const [uploadGrant, setUploadGrant] = useState("");
  const [uploadFile, setUploadFile] = useState("");
  const [uploadSelector, setUploadSelector] = useState("");
  const [downloadGrant, setDownloadGrant] = useState("");
  const [downloadFile, setDownloadFile] = useState("");
  const [downloadUrl, setDownloadUrl] = useState("");
  const [downloadSelector, setDownloadSelector] = useState("");
  const [fileResult, setFileResult] = useState<{
    action: "upload" | "download";
    file: string;
    size?: number;
  } | null>(null);

  const clearForms = useCallback(() => {
    setForms([]);
    setFormId("");
    setSubmitId("");
    setFormInstruction("");
    setSuggestion(null);
  }, []);

  useEffect(() => {
    if (request.current) window.clearTimeout(request.current.timeout);
    request.current = null;
    bridgeProof.current = null;
    setBusy(null);
    setOpened(false);
    tabNumbers.current.clear();
    setTabs([]);
    setSelected("");
    setFocusedTab("");
    setResult(null);
    setCapture(null);
    clearForms();
    setOperations([]);
    setSubmissionUncertain(false);
    setUncertainOperationId("");
    setGrants([]);
    setUploadGrant("");
    setUploadFile("");
    setUploadSelector("");
    setDownloadGrant("");
    setDownloadFile("");
    setDownloadUrl("");
    setDownloadSelector("");
    setFileResult(null);
    setError("");
  }, [clearForms, deviceId]);

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
    const receiveDocx = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          device_id: string;
          grants?: DirectoryGrant[];
        }>
      ).detail;
      if (detail?.device_id === deviceId && detail.grants) {
        setGrants(detail.grants);
      }
    };
    window.addEventListener(READY_EVENT, receive);
    window.addEventListener("colink:docx-status", receiveDocx);
    try {
      invokeDesktop(
        `wails:event:emit:desktop:docx:${encodeURIComponent(
          JSON.stringify({ action: "list" }),
        )}`,
      );
    } catch {
      // Desktop bridge may not be loaded yet or during tests
    }
    connect();
    return () => {
      window.removeEventListener(READY_EVENT, receive);
      window.removeEventListener("colink:docx-status", receiveDocx);
      if (hello.current) window.clearTimeout(hello.current.timeout);
      hello.current = null;
      bridgeProof.current = null;
    };
  }, [connect, deviceId]);

  const send = useCallback(
    (
      action: BrowserAction,
      payload: Record<string, unknown> = {},
      openUrlAfterLaunch?: string,
    ) => {
      const proof = bridgeProof.current;
      if (!proof || request.current) return;
      if (
        [
          "open",
          "list",
          "new_tab",
          "select_tab",
          "close_tab",
          "query",
          "capture",
          "forms",
        ].includes(action)
      ) {
        clearForms();
      }
      if (action === "suggest_form") setSuggestion(null);
      const id = requestId();
      const timeout = window.setTimeout(
        () => {
          const timedOut = request.current;
          request.current = null;
          bridgeProof.current = null;
          setBridge("unavailable");
          setBusy(null);
          setFocusedTab("");
          if (timedOut?.action === "submit_form") {
            clearForms();
            setSubmissionUncertain(true);
            setUncertainOperationId(timedOut.id);
          }
          setError(
            timedOut?.action === "submit_form" ? "" : "BROWSER_NO_RESPONSE",
          );
        },
        action === "query" || action === "suggest_form" ? 180_000 : 120_000,
      );
      request.current = {
        id,
        action,
        tabId: typeof payload.tab_id === "string" ? payload.tab_id : undefined,
        createsTab:
          action === "new_tab" ||
          (action === "open" &&
            typeof payload.url === "string" &&
            !!payload.url),
        openUrlAfterLaunch,
        timeout,
      };
      setError("");
      setBusy(action);
      if (action === "list" || action === "select_tab") setFocusedTab("");
      if (action === "query") setResult(null);
      if (action === "capture") setCapture(null);
      if (action === "upload" || action === "download") setFileResult(null);
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
                      Date.now() +
                      (action === "query" || action === "suggest_form"
                        ? 165_000
                        : 110_000),
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
    [clearForms],
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
        clearForms();
        setFileResult(null);
      };
      if (detail.error) {
        if (pending.action === "submit_form") {
          setError(
            detail.error === "BROWSER_NOT_AUTHORIZED" ? detail.error : "",
          );
          clearForms();
          setSubmissionUncertain(true);
          setUncertainOperationId(pending.id);
          if (detail.error !== "BROWSER_NOT_AUTHORIZED") {
            window.setTimeout(() => send("operations"), 0);
          }
        } else {
          setError(
            pending.action === "suggest_form" &&
              detail.error === "BROWSER_MODEL_FAILED"
              ? "BROWSER_FORM_SUGGEST_FAILED"
              : detail.error,
          );
        }
        if (
          detail.error === "BROWSER_NOT_OPEN" ||
          detail.error === "BROWSER_NOT_AUTHORIZED"
        ) {
          resetBrowser();
          if (detail.error === "BROWSER_NOT_AUTHORIZED") {
            setOperations([]);
          }
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
      if (pending.action === "operations") {
        const nextOperations = detail.operations ?? [];
        setOperations(nextOperations);
        if (
          nextOperations.some(
            (operation) => operation.id === uncertainOperationId,
          )
        ) {
          setSubmissionUncertain(false);
          setUncertainOperationId("");
        }
        return;
      }
      setOpened(true);
      if (pending.action === "forms") {
        setForms(detail.forms ?? []);
        setFormId("");
        setSubmitId("");
        setSuggestion(null);
        return;
      }
      if (pending.action === "suggest_form") {
        setSuggestion(detail);
        return;
      }
      if (pending.action === "submit_form") {
        if (detail.operation) {
          setOperations((previous) => [
            detail.operation!,
            ...previous.filter(
              (operation) => operation.id !== detail.operation!.id,
            ),
          ]);
          setSubmissionUncertain(false);
          setUncertainOperationId("");
        }
        clearForms();
        return;
      }
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
      if (pending.action === "upload" || pending.action === "download") {
        if (detail.file) {
          setFileResult({
            action: pending.action,
            file: detail.file,
            size: detail.size,
          });
        }
      }
    };
    window.addEventListener(STATUS_EVENT, receive);
    return () => window.removeEventListener(STATUS_EVENT, receive);
  }, [clearForms, deviceId, send, uncertainOperationId]);

  useEffect(() => {
    if (
      bridge === "ready" &&
      bridgeProof.current?.operation_deadline === true
    ) {
      send("operations");
    }
  }, [bridge, deviceId, send]);

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
  const selectStyle = {
    width: "100%",
    border: `1px solid ${token.colorBorder}`,
    borderRadius: token.borderRadius,
    background: token.colorBgContainer,
    color: token.colorText,
    font: "inherit",
    padding: "7px 11px",
  };
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
  const selectedForm = forms.find((form) => form.id === formId);
  const selectedSubmitter = selectedForm?.submitters.find(
    (submitter) => submitter.id === submitId,
  );
  const canSuggest = Boolean(
    canRead &&
      bridgeProof.current?.operation_deadline === true &&
      selectedForm &&
      selectedSubmitter &&
      formInstruction.trim() &&
      !blocked,
  );
  const uploadAttachment = () => {
    if (!selectedTab || !uploadGrant || !uploadFile.trim()) return;
    send("upload", {
      tab_id: selectedTab.id,
      url: selectedTab.url,
      grant_id: uploadGrant,
      file: uploadFile.trim(),
      ...(uploadSelector.trim() ? { selector: uploadSelector.trim() } : {}),
    });
  };
  const hasDownloadSource =
    Boolean(downloadUrl.trim()) !== Boolean(downloadSelector.trim());
  const downloadArtifact = () => {
    if (
      !selectedTab ||
      !downloadGrant ||
      !downloadFile.trim() ||
      !hasDownloadSource
    )
      return;
    send("download", {
      tab_id: selectedTab.id,
      url: selectedTab.url,
      grant_id: downloadGrant,
      file: downloadFile.trim(),
      ...(downloadUrl.trim() ? { download_url: downloadUrl.trim() } : {}),
      ...(downloadSelector.trim() ? { selector: downloadSelector.trim() } : {}),
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
    BROWSER_FORM_STALE: t(
      "localBrowser.errors.BROWSER_FORM_STALE",
      "页面表单已变化，请重新读取并核对目标。",
    ),
    BROWSER_NO_RESPONSE: t(
      "localBrowser.errors.BROWSER_NO_RESPONSE",
      "未收到桌面客户端回执，请重新连接后重试。",
    ),
    BROWSER_SERVICE_UNAVAILABLE: t(
      "localBrowser.errors.BROWSER_SERVICE_UNAVAILABLE",
      "浏览器服务暂不可用，请稍后重试。",
    ),
    BROWSER_FORM_SUGGEST_FAILED: t(
      "localBrowser.errors.BROWSER_FORM_SUGGEST_FAILED",
      "无法生成填写建议，请检查模型配置后重试。",
    ),
    BROWSER_ACTION_CHANGED: t(
      "localBrowser.errors.BROWSER_ACTION_CHANGED",
      "页面或目标已改变，请核对后重新确认。",
    ),
    BROWSER_TRANSFER_UNCERTAIN: t(
      "localBrowser.errors.BROWSER_TRANSFER_UNCERTAIN",
      "操作结果不确定，请先到浏览器核对再决定是否重试。",
    ),
    BROWSER_FILE_TOO_LARGE: t(
      "localBrowser.errors.BROWSER_FILE_TOO_LARGE",
      "文件超过 20 MiB 传输上限。",
    ),
    BROWSER_ELEMENT_NOT_FOUND: t(
      "localBrowser.errors.BROWSER_ELEMENT_NOT_FOUND",
      "浏览器未找到指定的文件输入框。",
    ),
    "file operation cancelled": t(
      "localBrowser.errors.OPERATION_CANCELLED",
      "已取消本次操作。",
    ),
    BROWSER_FILE_NOT_FOUND: t(
      "localBrowser.errors.FILE_NOT_FOUND",
      "授权文件不存在或无法读取。",
    ),
    BROWSER_FILE_SAVE_FAILED: t(
      "localBrowser.errors.FILE_SAVE_FAILED",
      "无法保存下载文件。",
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
                style={selectStyle}
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
              {bridgeProof.current?.operation_deadline === true && canRead && (
                <section aria-label={t("localBrowser.forms", "表单填写")}>
                  <Space
                    direction="vertical"
                    size={8}
                    style={{ width: "100%" }}
                  >
                    <Typography.Title level={5} style={{ margin: 0 }}>
                      {t("localBrowser.forms", "表单填写")}
                    </Typography.Title>
                    <Button
                      loading={busy === "forms"}
                      disabled={blocked}
                      onClick={() =>
                        selectedTab &&
                        send("forms", {
                          tab_id: selectedTab.id,
                          url: selectedTab.url,
                        })
                      }
                    >
                      {t("localBrowser.readForms", "读取表单")}
                    </Button>
                    {forms.length > 0 && (
                      <>
                        <label htmlFor={`${id}-forms`}>
                          {t("localBrowser.form", "表单")}
                        </label>
                        <select
                          id={`${id}-forms`}
                          value={formId}
                          disabled={blocked}
                          onChange={(event) => {
                            setFormId(event.target.value);
                            setSubmitId("");
                            setSuggestion(null);
                          }}
                          style={selectStyle}
                        >
                          <option value="">
                            {t("localBrowser.selectForm", "选择表单")}
                          </option>
                          {forms.map((form) => (
                            <option key={form.id} value={form.id}>
                              {displayFormText(form.label)}
                            </option>
                          ))}
                        </select>
                      </>
                    )}
                    {selectedForm && (
                      <>
                        <label htmlFor={`${id}-submitter`}>
                          {t("localBrowser.submitter", "提交动作")}
                        </label>
                        <select
                          id={`${id}-submitter`}
                          value={submitId}
                          disabled={
                            blocked || selectedForm.submitters.length === 0
                          }
                          onChange={(event) => {
                            setSubmitId(event.target.value);
                            setSuggestion(null);
                          }}
                          style={selectStyle}
                        >
                          <option value="">
                            {t("localBrowser.selectSubmitter", "选择提交动作")}
                          </option>
                          {selectedForm.submitters.map((submitter) => (
                            <option key={submitter.id} value={submitter.id}>
                              {displayFormText(submitter.label)}
                            </option>
                          ))}
                        </select>
                      </>
                    )}
                    {selectedSubmitter && (
                      <Space direction="vertical" size={2}>
                        <Typography.Text style={{ overflowWrap: "anywhere" }}>
                          {displayFormText(
                            displayAddress(selectedSubmitter.action_url),
                          )}
                        </Typography.Text>
                        <Typography.Text type="secondary">
                          {displayFormText(
                            selectedSubmitter.method.toUpperCase(),
                          )}{" "}
                          · {displayFormText(selectedSubmitter.target)}
                        </Typography.Text>
                      </Space>
                    )}
                    {selectedSubmitter && (
                      <>
                        <label htmlFor={`${id}-form-instruction`}>
                          {t("localBrowser.formInstruction", "填写任务")}
                        </label>
                        <Input.TextArea
                          id={`${id}-form-instruction`}
                          value={formInstruction}
                          maxLength={8000}
                          autoSize={{ minRows: 2, maxRows: 5 }}
                          onChange={(event) =>
                            setFormInstruction(event.target.value)
                          }
                        />
                        <Button
                          loading={busy === "suggest_form"}
                          disabled={!canSuggest}
                          onClick={() =>
                            selectedTab &&
                            send("suggest_form", {
                              tab_id: selectedTab.id,
                              url: selectedTab.url,
                              form_id: formId,
                              submit_id: submitId,
                              instruction: formInstruction.trim(),
                            })
                          }
                        >
                          {t("localBrowser.suggestForm", "生成填写建议")}
                        </Button>
                      </>
                    )}
                    {suggestion?.values && (
                      <section
                        aria-label={t("localBrowser.preview", "填写建议预览")}
                      >
                        <Space
                          direction="vertical"
                          size={6}
                          style={{ width: "100%" }}
                        >
                          <Typography.Text strong>
                            {displayFormText(
                              suggestion.form_label ||
                                selectedForm?.label ||
                                "",
                            )}
                          </Typography.Text>
                          <Typography.Text>
                            {displayFormText(
                              suggestion.submit_label ||
                                selectedSubmitter?.label ||
                                "",
                            )}
                          </Typography.Text>
                          <Typography.Text style={{ overflowWrap: "anywhere" }}>
                            {displayFormText(
                              displayAddress(
                                suggestion.action_url ||
                                  selectedSubmitter?.action_url ||
                                  "",
                              ),
                            )}
                          </Typography.Text>
                          <Typography.Text type="secondary">
                            {displayFormText(
                              suggestion.method?.toUpperCase() ||
                                selectedSubmitter?.method.toUpperCase() ||
                                "",
                            )}{" "}
                            ·{" "}
                            {displayFormText(
                              suggestion.target ||
                                selectedSubmitter?.target ||
                                "",
                            )}
                          </Typography.Text>
                          {(suggestion.fields ?? []).map((field) => {
                            const value = suggestion.values?.[field.id];
                            if (value === undefined) return null;
                            return (
                              <div key={field.id}>
                                <Typography.Text strong>
                                  {displayFormText(field.label)}:{" "}
                                </Typography.Text>
                                <Typography.Text>
                                  {formValue(field.value, t)} →{" "}
                                  {formValue(value, t)}
                                </Typography.Text>
                              </div>
                            );
                          })}
                          <Button
                            type="primary"
                            loading={busy === "submit_form"}
                            disabled={
                              blocked ||
                              !suggestion.snapshot_id ||
                              suggestion.form_id !== formId ||
                              suggestion.submit_id !== submitId ||
                              Object.keys(suggestion.values).length === 0
                            }
                            onClick={() =>
                              selectedTab &&
                              send("submit_form", {
                                tab_id: selectedTab.id,
                                url: selectedTab.url,
                                form_id: suggestion.form_id,
                                submit_id: suggestion.submit_id,
                                snapshot_id: suggestion.snapshot_id,
                                values: suggestion.values,
                              })
                            }
                          >
                            {t("localBrowser.submitForm", "提交表单")}
                          </Button>
                        </Space>
                      </section>
                    )}
                  </Space>
                </section>
              )}

              <Card
                size="small"
                title={t("localBrowser.uploadTitle", "上传网页附件")}
              >
                <Space direction="vertical" size={8} style={{ width: "100%" }}>
                  <Typography.Text type="secondary">
                    {t(
                      "localBrowser.uploadHint",
                      "从授权目录选择文件上传到当前网页。",
                    )}
                  </Typography.Text>
                  <label htmlFor={`${id}-upload-grant`}>
                    {t("localBrowser.uploadGrant", "选择上传目录授权")}
                  </label>
                  <select
                    id={`${id}-upload-grant`}
                    aria-label={t(
                      "localBrowser.uploadGrant",
                      "选择上传目录授权",
                    )}
                    value={uploadGrant}
                    disabled={!grants.length || blocked}
                    onChange={(e) => setUploadGrant(e.target.value)}
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
                    {!uploadGrant && (
                      <option value="" disabled>
                        {grants.length
                          ? t("localBrowser.selectGrant", "选择目录授权")
                          : t(
                              "localBrowser.noGrants",
                              "暂无可用目录授权，请在“本地文件”中授权目录",
                            )}
                      </option>
                    )}
                    {grants.map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.path} ({g.mode === "rw" ? "读写" : "只读"})
                      </option>
                    ))}
                  </select>
                  <label htmlFor={`${id}-upload-file`}>
                    {t("localBrowser.uploadFile", "上传文件名")}
                  </label>
                  <Input
                    id={`${id}-upload-file`}
                    aria-label={t("localBrowser.uploadFile", "上传文件名")}
                    placeholder="document.pdf"
                    value={uploadFile}
                    disabled={blocked}
                    onChange={(e) => setUploadFile(e.target.value)}
                  />
                  <label htmlFor={`${id}-upload-selector`}>
                    {t("localBrowser.uploadSelector", "文件输入选择器（可选）")}
                  </label>
                  <Input
                    id={`${id}-upload-selector`}
                    aria-label={t(
                      "localBrowser.uploadSelector",
                      "文件输入选择器（可选）",
                    )}
                    placeholder='input[type="file"]'
                    value={uploadSelector}
                    disabled={blocked}
                    onChange={(e) => setUploadSelector(e.target.value)}
                  />
                  <div>
                    <Button
                      loading={busy === "upload"}
                      disabled={
                        !canRead ||
                        !uploadGrant ||
                        !uploadFile.trim() ||
                        blocked
                      }
                      onClick={uploadAttachment}
                    >
                      {t("localBrowser.uploadBtn", "上传附件到页面")}
                    </Button>
                  </div>
                </Space>
              </Card>

              <Card
                size="small"
                title={t("localBrowser.downloadTitle", "下载网页产物")}
              >
                <Space direction="vertical" size={8} style={{ width: "100%" }}>
                  <Typography.Text type="secondary">
                    {t(
                      "localBrowser.downloadHint",
                      "请填写同站下载链接或链接选择器（二选一）。网页动作和文件覆盖分别确认。",
                    )}
                  </Typography.Text>
                  <label htmlFor={`${id}-download-grant`}>
                    {t("localBrowser.downloadGrant", "选择下载目录授权")}
                  </label>
                  <select
                    id={`${id}-download-grant`}
                    aria-label={t(
                      "localBrowser.downloadGrant",
                      "选择下载目录授权",
                    )}
                    value={downloadGrant}
                    disabled={
                      !grants.filter((g) => g.mode === "rw").length || blocked
                    }
                    onChange={(e) => setDownloadGrant(e.target.value)}
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
                    {!downloadGrant && (
                      <option value="" disabled>
                        {grants.filter((g) => g.mode === "rw").length
                          ? t("localBrowser.selectGrant", "选择目录授权")
                          : t(
                              "localBrowser.noRwGrants",
                              "暂无可写目录授权，请在“本地文件”中授权读写目录",
                            )}
                      </option>
                    )}
                    {grants
                      .filter((g) => g.mode === "rw")
                      .map((g) => (
                        <option key={g.id} value={g.id}>
                          {g.path}
                        </option>
                      ))}
                  </select>
                  <label htmlFor={`${id}-download-file`}>
                    {t("localBrowser.downloadFile", "保存文件名")}
                  </label>
                  <Input
                    id={`${id}-download-file`}
                    aria-label={t("localBrowser.downloadFile", "保存文件名")}
                    placeholder="report.xlsx"
                    value={downloadFile}
                    disabled={blocked}
                    onChange={(e) => setDownloadFile(e.target.value)}
                  />
                  <label htmlFor={`${id}-download-url`}>
                    {t("localBrowser.downloadUrl", "下载链接（可选）")}
                  </label>
                  <Input
                    id={`${id}-download-url`}
                    aria-label={t(
                      "localBrowser.downloadUrl",
                      "下载链接（可选）",
                    )}
                    placeholder="https://intranet.example/download/report.xlsx"
                    value={downloadUrl}
                    disabled={blocked}
                    onChange={(e) => setDownloadUrl(e.target.value)}
                  />
                  <label htmlFor={`${id}-download-selector`}>
                    {t(
                      "localBrowser.downloadSelector",
                      "下载链接选择器（可选）",
                    )}
                  </label>
                  <Input
                    id={`${id}-download-selector`}
                    aria-label={t(
                      "localBrowser.downloadSelector",
                      "下载链接选择器（可选）",
                    )}
                    placeholder="#export-link"
                    value={downloadSelector}
                    disabled={blocked}
                    onChange={(e) => setDownloadSelector(e.target.value)}
                  />
                  <div>
                    <Button
                      loading={busy === "download"}
                      disabled={
                        !canRead ||
                        !downloadGrant ||
                        !downloadFile.trim() ||
                        !hasDownloadSource ||
                        blocked
                      }
                      onClick={downloadArtifact}
                    >
                      {t("localBrowser.downloadBtn", "下载产物到授权目录")}
                    </Button>
                  </div>
                </Space>
              </Card>
            </>
          )}
          {(submissionUncertain ||
            operations.some((operation) =>
              operationLabel(operation.status, t),
            )) && (
            <section
              aria-label={t("localBrowser.operationStatus", "表单操作状态")}
            >
              <Space direction="vertical" size={6} style={{ width: "100%" }}>
                {submissionUncertain && (
                  <Typography.Text role="status">
                    {t(
                      "localBrowser.operationNoResponse",
                      "未收到提交回执，请先检查业务页面，之后再决定是否发起新任务。",
                    )}
                  </Typography.Text>
                )}
                {operations.map((operation) => {
                  const label = operationLabel(operation.status, t);
                  if (!label) return null;
                  return (
                    <Typography.Text role="status" key={operation.id}>
                      {label} {displayFormText(operation.url)}
                    </Typography.Text>
                  );
                })}
              </Space>
            </section>
          )}
          {error && (
            <Alert
              showIcon
              type="error"
              message={
                errors[error] ??
                t(
                  `localBrowser.errors.${error}`,
                  error ||
                    t(
                      "localBrowser.errors.BROWSER_TASK_FAILED",
                      "无法完成浏览器操作，请重试。",
                    ),
                )
              }
            />
          )}
          {fileResult && (
            <Alert
              showIcon
              type="success"
              message={
                fileResult.action === "upload"
                  ? t(
                      "localBrowser.uploadSuccess",
                      "已为网页选择附件：{{file}}。请在网页上核对提交结果。",
                      {
                        file: fileResult.file,
                      },
                    )
                  : t(
                      "localBrowser.downloadSuccess",
                      "产物已成功保存：{{file}}（{{size}} 字节）",
                      {
                        file: fileResult.file,
                        size: fileResult.size ?? 0,
                      },
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
