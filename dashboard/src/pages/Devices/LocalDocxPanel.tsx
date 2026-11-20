import { useEffect, useId, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Input,
  Space,
  Tag,
  Typography,
  theme,
} from "antd";
import { FileText, FolderPlus } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  localExpertsApi,
  type LocalExpert,
} from "../../api/modules/localExperts";
import { apiErrorMessage } from "../../utils/apiError";
import LocalArtifactPreview from "./LocalArtifactPreview";
import styles from "./LocalDocxPanel.module.less";

interface Grant {
  id: string;
  path: string;
  mode: "ro" | "rw";
  destructive?: boolean;
  overwrite?: boolean;
}
type Operation = "run" | "overwrite" | "delete";
interface LocalTask {
  format?: "docx" | "xlsx" | "ocr" | "pptx" | "pdf";
  id: string;
  instruction: string;
  agent_id?: string;
  source: string;
  output: string;
  operation?: Operation;
  status: string;
  error?: string;
  recovery?: string;
  steps?: string[];
}
interface NativeState {
  device_id: string;
  grants: Grant[];
  tasks: LocalTask[];
  busy: boolean;
  managed_directory?: boolean;
  error?: string;
}

function send(command: Record<string, string>) {
  const invoke = (
    window as Window & { _wails?: { invoke?: (message: string) => void } }
  )._wails?.invoke;
  if (!invoke) throw new Error("Desktop client unavailable");
  invoke(
    `wails:event:emit:desktop:docx:${encodeURIComponent(
      JSON.stringify(command),
    )}`,
  );
}

export default function LocalDocxPanel({
  deviceId,
  connected = true,
  disconnectCode,
  artifactsOnly = false,
}: {
  deviceId: string;
  connected?: boolean;
  disconnectCode?: string;
  artifactsOnly?: boolean;
}) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const id = useId();
  const [state, setState] = useState<NativeState>({
    device_id: deviceId,
    grants: [],
    tasks: [],
    busy: false,
  });
  const [experts, setExperts] = useState<LocalExpert[]>([]);
  const [expertId, setExpertId] = useState("");
  const [expertError, setExpertError] = useState<unknown>(null);
  const [format, setFormat] = useState<
    "docx" | "xlsx" | "ocr" | "pptx" | "pdf"
  >("docx");
  const [operation, setOperation] = useState<Operation>("run");
  const [lastRequest, setLastRequest] = useState<{
    key: string;
    id: string;
  } | null>(null);
  const contentKey = {
    docx: "localDocx",
    xlsx: "localXlsx",
    ocr: "localOcr",
    pptx: "localPptx",
    pdf: "localPdf",
  }[format];
  const [sourceGrant, setSourceGrant] = useState("");
  const [outputGrant, setOutputGrant] = useState("");
  const [source, setSource] = useState("");
  const [output, setOutput] = useState("");
  const [instruction, setInstruction] = useState("");
  const [previewId, setPreviewId] = useState<string | null>(null);
  const previewRegion = useRef<HTMLElement>(null);
  const previewTrigger = useRef<HTMLElement | null>(null);

  useEffect(() => {
    setState({ device_id: deviceId, grants: [], tasks: [], busy: false });
    setFormat("docx");
    setPreviewId(null);
    setOperation("run");
    setLastRequest(null);
    setExpertId("");
    setExperts([]);
    setExpertError(null);
    let active = true;
    if (!artifactsOnly)
      void localExpertsApi
        .list()
        .then((items) => {
          if (active) setExperts(items);
        })
        .catch((error: unknown) => {
          if (active) setExpertError(error);
        });
    setSourceGrant("");
    setOutputGrant("");
    setSource("");
    setOutput("");
    setInstruction("");
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<NativeState>).detail;
      if (detail?.device_id === deviceId)
        setState({
          ...detail,
          grants: detail.grants ?? [],
          tasks: detail.tasks ?? [],
        });
    };
    window.addEventListener("colink:docx-status", receive);
    return () => {
      active = false;
      window.removeEventListener("colink:docx-status", receive);
    };
  }, [artifactsOnly, deviceId]);

  useEffect(() => {
    if (connected) send({ action: "list" });
  }, [deviceId, connected]);

  const dispatch = (command: Record<string, string>) => {
    if (!connected) return;
    setState((previous) => ({ ...previous, busy: true, error: undefined }));
    try {
      send(
        format !== "docx" &&
          command.action !== "revoke" &&
          command.action !== "cancel"
          ? { ...command, format }
          : command,
      );
    } catch (error) {
      setState((previous) => ({
        ...previous,
        busy: false,
        error: String(error),
      }));
    }
  };
  const selectStyle = {
    width: "100%",
    padding: "7px 11px",
    borderRadius: token.borderRadius,
    border: "1px solid var(--fn-border-input)",
    background: token.colorBgContainer,
    color: token.colorText,
    font: "inherit",
  };
  const statuses: Record<string, string> = {
    running: t("localDocx.running", "处理中"),
    awaiting_confirmation: t("localDocx.awaitingConfirmation", "等待确认"),
    completed: t("localDocx.completed", "已完成"),
    cancelled: t("localDocx.cancelled", "已取消"),
    failed: t("localDocx.failed", "失败"),
    interrupted: t("localDocx.interrupted", "需要核对"),
  };
  const statusColors: Record<string, string> = {
    running: "processing",
    awaiting_confirmation: "warning",
    completed: "success",
    failed: "error",
    cancelled: "default",
    interrupted: "volcano",
  };
  const operations: Record<Operation, string> = {
    run: t("localDocx.createOperation", "生成新文件"),
    overwrite: t("localDocx.overwriteOperation", "覆盖现有文件"),
    delete: t("localDocx.deleteOperation", "删除文件"),
  };
  const errorMessage = (message: string) =>
    /^OCR_[A-Z_]+$/.test(message)
      ? t(`localOcr.errors.${message}`, t("localOcr.errors.OCR_INTERNAL"))
      : /^PDF_[A-Z_]+$/.test(message)
      ? t(`localPdf.errors.${message}`, t("localPdf.errors.PDF_TASK_FAILED"))
      : /^PPTX_[A-Z_]+$/.test(message)
      ? t(`localPptx.errors.${message}`, t("localPptx.errors.PPTX_TASK_FAILED"))
      : message;
  const destructive = operation !== "run";
  const deleting = operation === "delete";
  const supportsOverwrite = state.grants.some(
    (grant) => grant.mode === "rw" && grant.overwrite,
  );
  const targetGrants = state.grants.filter(
    (grant) =>
      grant.mode === "rw" &&
      (!destructive || (deleting ? grant.destructive : grant.overwrite)),
  );
  const saveDirectory = targetGrants.find((grant) => grant.id === outputGrant);
  const grantForArtifact = (task: LocalTask) =>
    task.status === "completed" && task.operation !== "delete" && task.output
      ? state.grants.find((grant) => {
          const path = task.output.replaceAll("\\", "/");
          const directory = grant.path.replaceAll("\\", "/").replace(/\/$/, "");
          return path.slice(0, path.lastIndexOf("/")) === directory;
        })
      : undefined;
  const previewTask = connected
    ? state.tasks.find(
        (task) => task.id === previewId && grantForArtifact(task),
      )
    : undefined;
  const activePreviewId = previewTask?.id;
  useEffect(() => {
    if (activePreviewId) previewRegion.current?.focus();
    else setPreviewId(null);
  }, [activePreviewId]);

  const previewButton = (task: LocalTask) => (
    <Button
      size="small"
      onClick={(event) => {
        previewTrigger.current = event.currentTarget;
        if (activePreviewId === task.id) previewRegion.current?.focus();
        else setPreviewId(task.id);
      }}
    >
      {t("localDocx.previewArtifact")}
    </Button>
  );
  const preview = previewTask && (
    <section
      aria-label={t("localDocx.previewArtifact")}
      ref={previewRegion}
      tabIndex={-1}
    >
      <Space wrap>
        <Typography.Title level={5}>
          {t("localDocx.previewArtifact")}
        </Typography.Title>
        <Button
          size="small"
          onClick={() => {
            setPreviewId(null);
            previewTrigger.current?.focus();
          }}
        >
          {t("common.close")}
        </Button>
      </Space>
      <div>
        <Typography.Text style={{ overflowWrap: "anywhere" }}>
          {previewTask.output}
        </Typography.Text>
      </div>
      <div className={styles.artifactPreview}>
        <LocalArtifactPreview
          key={previewTask.id}
          deviceId={deviceId}
          taskId={previewTask.id}
          filename={previewTask.output.split(/[/\\]/).at(-1) ?? ""}
        />
      </div>
    </section>
  );

  const hasSource =
    source.trim() && state.grants.some((grant) => grant.id === sourceGrant);
  const hasExpert =
    !expertId ||
    experts.some(
      (expert) =>
        expert.agent_id === expertId &&
        expert.actions.includes(`local_${format}`),
    );
  const canRun =
    connected &&
    targetGrants.some((grant) => grant.id === outputGrant) &&
    output.trim() &&
    (deleting ||
      (hasSource && (format === "ocr" || (instruction.trim() && hasExpert))));

  const taskCommand: Record<string, string> = {
    action: operation,
    output_grant: outputGrant,
    output: output.trim(),
  };
  if (!deleting) {
    taskCommand.source_grant = sourceGrant;
    taskCommand.source = source.trim();
    taskCommand.instruction = format === "ocr" ? "" : instruction.trim();
    if (format !== "ocr" && expertId) taskCommand.agent_id = expertId;
  }
  const requestKey = destructive ? JSON.stringify([format, taskCommand]) : "";
  const matchingRequest = lastRequest?.key === requestKey ? lastRequest : null;
  const canStartNew =
    matchingRequest &&
    state.tasks.some(
      (task) =>
        task.id === matchingRequest.id &&
        ["completed", "cancelled", "failed", "interrupted"].includes(
          task.status,
        ),
    );

  const submitTask = (startNew = false) => {
    if (!canRun || state.busy) return;
    if (!destructive) {
      dispatch(taskCommand);
      return;
    }
    let requestId = startNew ? undefined : matchingRequest?.id;
    if (!requestId) {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      requestId = Array.from(bytes, (byte) =>
        byte.toString(16).padStart(2, "0"),
      ).join("");
      setLastRequest({ key: requestKey, id: requestId });
    }
    dispatch({ ...taskCommand, request_id: requestId });
  };

  if (artifactsOnly) {
    const artifacts = connected ? state.tasks.filter(grantForArtifact) : [];
    return (
      <div className={styles.taskLayout}>
        <Card title={t("localDocx.artifacts")}>
          {artifacts.length ? (
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              {artifacts.map((task) => (
                <div key={task.id}>
                  <Typography.Text
                    copyable
                    style={{ overflowWrap: "anywhere" }}
                  >
                    {task.output}
                  </Typography.Text>{" "}
                  {previewButton(task)}
                </div>
              ))}
            </Space>
          ) : (
            <Typography.Text type="secondary">
              {t("localDocx.noArtifacts")}
            </Typography.Text>
          )}
        </Card>
        {preview && <div className={styles.saveLocation}>{preview}</div>}
      </div>
    );
  }

  return (
    <Card
      title={
        <Space>
          <FileText size={18} />
          {t("localDocx.title", "本机文档任务")}
        </Space>
      }
    >
      <Space direction="vertical" size={20} style={{ width: "100%" }}>
        {!connected && (
          <Alert
            type="warning"
            showIcon
            message={
              disconnectCode === "DEVICE_REVOKED"
                ? t(
                    "apiErrors.DEVICE_REVOKED",
                    "设备授权已撤销，请联系管理员重新批准。",
                  )
                : disconnectCode === "USER_DISABLED"
                ? t("apiErrors.USER_DISABLED", "账号已禁用。")
                : disconnectCode === "TOKEN_EXPIRED"
                ? t("apiErrors.TOKEN_EXPIRED", "登录已过期，请重新登录。")
                : t(
                    "localDocx.deviceDisconnected",
                    "设备连接已断开，受控操作已暂停。请在上方重新接入设备。",
                  )
            }
          />
        )}
        <div>
          <label htmlFor={`${id}-format`}>
            {t("localDocx.format", "文件类型")}
          </label>
          <select
            id={`${id}-format`}
            value={format}
            disabled={!connected || state.busy}
            style={selectStyle}
            onChange={(event) => {
              setFormat(
                event.target.value as "docx" | "xlsx" | "ocr" | "pptx" | "pdf",
              );
              setOperation("run");
              setExpertId("");
              setSource("");
              setOutput("");
              setInstruction("");
            }}
          >
            <option value="docx">DOCX</option>
            <option value="xlsx">XLSX</option>
            <option value="pptx">PPTX</option>
            <option value="pdf">{t("localPdf.format", "文字 PDF")}</option>
            <option value="ocr">
              {t("localOcr.format", "扫描 PDF（OCR）")}
            </option>
          </select>
        </div>
        {(format === "docx" || format === "xlsx") && (
          <div>
            <label htmlFor={`${id}-operation`}>
              {t("localDocx.operation", "文件操作")}
            </label>
            <select
              id={`${id}-operation`}
              value={operation}
              disabled={!connected || state.busy}
              style={selectStyle}
              onChange={(event) => {
                setOperation(event.target.value as Operation);
                setOutputGrant("");
              }}
            >
              <option value="run">{operations.run}</option>
              {(supportsOverwrite || operation === "overwrite") && (
                <option value="overwrite" disabled={!supportsOverwrite}>
                  {operations.overwrite}
                </option>
              )}
              <option value="delete">{operations.delete}</option>
            </select>
          </div>
        )}
        {format !== "ocr" && !deleting && (
          <>
            {expertError ? (
              <Alert
                type="error"
                message={apiErrorMessage(
                  expertError,
                  t("localExperts.loadFailed", "无法加载私人专家"),
                  t,
                )}
              />
            ) : null}
            <div>
              <label htmlFor={`${id}-expert`}>
                {t("localExperts.taskExpert", "任务专家")}
              </label>
              <select
                id={`${id}-expert`}
                style={selectStyle}
                value={expertId}
                disabled={!connected || state.busy}
                onChange={(event) => setExpertId(event.target.value)}
              >
                <option value="">
                  {t("localExperts.noExpert", "不使用专家")}
                </option>
                {experts
                  .filter((expert) =>
                    expert.actions.includes(`local_${format}`),
                  )
                  .map((expert) => (
                    <option key={expert.agent_id} value={expert.agent_id}>
                      {expert.name}
                    </option>
                  ))}
              </select>
            </div>
          </>
        )}
        <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
          {deleting
            ? t("localDocx.deletePrivacy", "仅删除目标目录中的一个文件。")
            : t(
                `${contentKey}.privacy`,
                format === "ocr"
                  ? "原件和识别文字均保留在本机，不发送到模型；文字成果写入授权目录，不覆盖已有文件。"
                  : "原始文件保留在本机。所选文档的正文经服务端发送到已配置模型。新建任务遇到同名文件时停止。",
              )}
        </Typography.Paragraph>
        {!deleting && (
          <Typography.Text type="secondary">
            {t(
              `${contentKey}.limits`,
              format === "ocr"
                ? "支持授权目录直属的扫描 PDF，不超过 20 MB、20 页和 60,000 字符。识别结果按页写入 TXT，请对照原件核对；资源缺失或识别失败时停止并报错。"
                : format === "pdf"
                ? "仅读取 PDF 中可选的文字，图片内容不会识别。"
                : "支持授权目录直属的 DOCX 文件（不含子目录），文件不超过 20 MB、正文不超过 60,000 字符；生成基础正文，不保留原版式。",
            )}
          </Typography.Text>
        )}
        <Space wrap>
          <Button
            icon={<FolderPlus size={16} />}
            disabled={!connected || state.busy}
            onClick={() => dispatch({ action: "grant", mode: "ro" })}
          >
            {t("localDocx.grantRead", "选择只读目录")}
          </Button>
          <Button
            icon={<FolderPlus size={16} />}
            disabled={!connected || state.busy}
            onClick={() => dispatch({ action: "grant", mode: "rw" })}
          >
            {t("localDocx.grantWrite", "选择读写目录")}
          </Button>
          {format !== "ocr" && state.managed_directory && (
            <Button
              icon={<FolderPlus size={16} />}
              disabled={!connected || state.busy}
              onClick={() => dispatch({ action: "grant_managed" })}
            >
              {t("localDocx.grantManaged", "添加受管目录")}
            </Button>
          )}
          <Button
            disabled={!connected || state.busy}
            onClick={() => dispatch({ action: "list" })}
          >
            {t("localDocx.refresh", "刷新本机记录")}
          </Button>
        </Space>
        {state.grants.length > 0 && (
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            {state.grants.map((grant) => (
              <Space key={grant.id} wrap>
                <Tag color={grant.mode === "rw" ? "blue" : "default"}>
                  {grant.mode === "rw"
                    ? t("localDocx.rw", "读写")
                    : t("localDocx.ro", "只读")}
                </Tag>
                <Typography.Text style={{ overflowWrap: "anywhere" }}>
                  {grant.path}
                </Typography.Text>
                <Button
                  size="small"
                  aria-label={t("localDocx.revokeGrant", {
                    path: grant.path,
                    defaultValue: "撤销 {{path}} 的目录授权",
                  })}
                  disabled={!connected}
                  onClick={() =>
                    dispatch({ action: "revoke", grant_id: grant.id })
                  }
                >
                  {t("localDocx.revoke", "撤销")}
                </Button>
              </Space>
            ))}
          </Space>
        )}
        {state.error && (
          <Alert type="error" showIcon message={errorMessage(state.error)} />
        )}
        {destructive && (
          <Alert
            type="info"
            showIcon
            message={t(
              "localDocx.confirmNative",
              "请在本机弹窗核对目标文件并确认。",
            )}
          />
        )}
        <div className={styles.taskLayout}>
          <div className={styles.taskBody}>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                submitTask();
              }}
            >
              <fieldset
                disabled={state.busy || !connected}
                style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}
              >
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      "repeat(auto-fit, minmax(min(100%, 360px), 1fr))",
                    gap: 16,
                  }}
                >
                  {!deleting && (
                    <div>
                      <label htmlFor={`${id}-source-grant`}>
                        {t("localDocx.sourceGrant", "资料目录")}
                      </label>
                      <select
                        id={`${id}-source-grant`}
                        value={sourceGrant}
                        onChange={(event) => setSourceGrant(event.target.value)}
                        style={selectStyle}
                        required
                      >
                        <option value="">
                          {t("localDocx.select", "请选择授权目录")}
                        </option>
                        {state.grants.map((grant) => (
                          <option key={grant.id} value={grant.id}>
                            {grant.path}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  <div>
                    <label htmlFor={`${id}-output-grant`}>
                      {destructive
                        ? t("localDocx.targetGrant", "目标目录")
                        : t("localDocx.outputGrant", "成果目录")}
                    </label>
                    <select
                      id={`${id}-output-grant`}
                      value={outputGrant}
                      onChange={(event) => setOutputGrant(event.target.value)}
                      style={selectStyle}
                      required
                    >
                      <option value="">
                        {t("localDocx.select", "请选择授权目录")}
                      </option>
                      {targetGrants.map((grant) => (
                        <option key={grant.id} value={grant.id}>
                          {grant.path}
                        </option>
                      ))}
                    </select>
                  </div>
                  {!deleting && (
                    <div>
                      <label htmlFor={`${id}-source`}>
                        {t("localDocx.source", "源文件名")}
                      </label>
                      <Input
                        id={`${id}-source`}
                        value={source}
                        onChange={(event) => setSource(event.target.value)}
                        placeholder={t(
                          `${contentKey}.sourceExample`,
                          format === "ocr"
                            ? "例如：扫描资料.pdf"
                            : "例如：会议记录.docx",
                        )}
                        maxLength={240}
                        required
                      />
                    </div>
                  )}
                  <div>
                    <label htmlFor={`${id}-output`}>
                      {destructive
                        ? t("localDocx.target", "目标文件名")
                        : t("localDocx.output", "新文件名")}
                    </label>
                    <Input
                      id={`${id}-output`}
                      value={output}
                      onChange={(event) => setOutput(event.target.value)}
                      placeholder={t(
                        `${contentKey}.outputExample`,
                        format === "ocr"
                          ? "例如：识别文字.txt"
                          : "例如：会议纪要.docx",
                      )}
                      maxLength={240}
                      required
                    />
                  </div>
                </div>
                {format !== "ocr" && !deleting && (
                  <div style={{ marginTop: 16, marginBottom: 16 }}>
                    <label htmlFor={`${id}-instruction`}>
                      {t("localDocx.instruction", "任务描述")}
                    </label>
                    <Input.TextArea
                      id={`${id}-instruction`}
                      value={instruction}
                      onChange={(event) => setInstruction(event.target.value)}
                      autoSize={{ minRows: 3, maxRows: 8 }}
                      maxLength={8000}
                      required
                      placeholder={t(
                        `${contentKey}.instructionExample`,
                        "整理所选文档，列出结论、待办和负责人。",
                      )}
                    />
                  </div>
                )}
                <Space
                  wrap
                  style={{ marginTop: format === "ocr" || deleting ? 16 : 0 }}
                >
                  <Button
                    type="primary"
                    htmlType="submit"
                    loading={state.busy}
                    disabled={!canRun}
                  >
                    {operation === "overwrite"
                      ? t("localDocx.requestOverwrite", "请求覆盖")
                      : deleting
                      ? t("localDocx.requestDelete", "请求删除")
                      : t(
                          `${contentKey}.run`,
                          format === "ocr"
                            ? "识别扫描 PDF"
                            : format === "xlsx"
                            ? "生成 XLSX"
                            : format === "pptx"
                            ? "生成 PPTX"
                            : format === "pdf"
                            ? "生成 PDF"
                            : "生成 DOCX",
                        )}
                  </Button>
                  {canStartNew && (
                    <Button disabled={!canRun} onClick={() => submitTask(true)}>
                      {t("localDocx.startNewTask", "新建本次任务")}
                    </Button>
                  )}
                </Space>
              </fieldset>
            </form>
            {state.tasks.length > 0 && (
              <section
                aria-label={t("localDocx.history", "本机任务记录")}
                aria-live="polite"
              >
                <Typography.Title level={5}>
                  {t("localDocx.history", "本机任务记录")}
                </Typography.Title>
                <Space direction="vertical" size={12} style={{ width: "100%" }}>
                  {state.tasks.map((task) => (
                    <div
                      key={task.id}
                      style={{
                        borderTop: `1px solid ${token.colorBorderSecondary}`,
                        paddingTop: 12,
                      }}
                    >
                      <Space wrap align="center" style={{ marginBottom: 4 }}>
                        <Tag color={statusColors[task.status] ?? "default"}>
                          {statuses[task.status] ?? task.status}
                        </Tag>
                        <Tag>{(task.format ?? "docx").toUpperCase()}</Tag>
                        {task.operation && task.operation !== "run" && (
                          <Tag>{operations[task.operation]}</Tag>
                        )}
                        {(task.status === "running" ||
                          task.status === "awaiting_confirmation") && (
                          <Button
                            size="small"
                            danger
                            disabled={!connected}
                            onClick={() =>
                              dispatch({ action: "cancel", task_id: task.id })
                            }
                          >
                            {t("localDocx.cancelTask", "取消任务")}
                          </Button>
                        )}
                      </Space>
                      <div>
                        <Typography.Text strong>
                          {task.instruction}
                        </Typography.Text>
                      </div>
                      {task.source && (
                        <div>
                          {!task.output &&
                            (task.status === "failed" ||
                              task.status === "cancelled") && (
                              <Typography.Text
                                type="secondary"
                                style={{ fontSize: 12, marginRight: 4 }}
                              >
                                {t("localDocx.retainedArtifact", "可用产物：")}
                              </Typography.Text>
                            )}
                          <Typography.Text type="secondary">
                            {task.source}
                          </Typography.Text>
                        </div>
                      )}
                      {task.output && (
                        <div>
                          {(task.status === "failed" ||
                            task.status === "cancelled") && (
                            <Typography.Text
                              type="secondary"
                              style={{ fontSize: 12, marginRight: 4 }}
                            >
                              {t("localDocx.retainedArtifact", "可用产物：")}
                            </Typography.Text>
                          )}
                          <Typography.Text
                            copyable
                            style={{ overflowWrap: "anywhere" }}
                          >
                            {task.output}
                          </Typography.Text>
                          {connected &&
                            grantForArtifact(task) &&
                            previewButton(task)}
                        </div>
                      )}
                      {task.status === "cancelled" && (
                        <Alert
                          type="info"
                          showIcon
                          style={{ marginTop: 8 }}
                          message={t(
                            "localDocx.cancelledHint",
                            "任务已取消，未继续后续动作；已保留可用产物。",
                          )}
                        />
                      )}
                      {task.status === "awaiting_confirmation" && (
                        <Alert
                          type="warning"
                          showIcon
                          style={{ marginTop: 8 }}
                          message={t(
                            "localDocx.awaitingHint",
                            "任务待确认：待确认动作不会自动执行，请在本机弹窗确认，或取消任务后重新发起。",
                          )}
                        />
                      )}
                      {task.status === "interrupted" && (
                        <Alert
                          type="warning"
                          showIcon
                          style={{ marginTop: 8 }}
                          message={
                            task.error
                              ? errorMessage(task.error)
                              : t(
                                  "localDocx.interruptedHint",
                                  "任务中断或结果不明，已保留已有文件，请核对目标文件后重试。",
                                )
                          }
                        />
                      )}
                      {task.status === "failed" && task.error && (
                        <Alert
                          type="error"
                          showIcon
                          style={{ marginTop: 8 }}
                          message={errorMessage(task.error)}
                        />
                      )}
                      {task.steps && task.steps.length > 0 && (
                        <div style={{ marginTop: 6 }}>
                          <Typography.Text
                            type="secondary"
                            style={{ fontSize: 12 }}
                          >
                            {t("localDocx.completedSteps", "已完成步骤：")}
                          </Typography.Text>
                          <ul
                            style={{
                              margin: "2px 0 0 16px",
                              padding: 0,
                              fontSize: 12,
                              color: token.colorTextSecondary,
                            }}
                          >
                            {task.steps.map((st, idx) => (
                              <li key={idx}>
                                {t(`localDocx.stepLabels.${st}`, st)}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  ))}
                </Space>
              </section>
            )}
          </div>
          <div className={styles.saveLocation}>
            <aside aria-label={t("localDocx.saveLocation")}>
              <Typography.Title level={5}>
                {t("localDocx.saveLocation")}
              </Typography.Title>
              {saveDirectory ? (
                <Typography.Text style={{ overflowWrap: "anywhere" }}>
                  {saveDirectory.path}
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">
                  {t("localDocx.chooseSaveLocation")}
                </Typography.Text>
              )}
            </aside>
            {preview}
          </div>
        </div>
      </Space>
    </Card>
  );
}
