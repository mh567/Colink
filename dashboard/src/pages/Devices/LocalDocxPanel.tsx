import { useEffect, useId, useState } from "react";
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

interface Grant {
  id: string;
  path: string;
  mode: "ro" | "rw";
  destructive?: boolean;
  overwrite?: boolean;
}
type Operation = "run" | "overwrite" | "delete";
interface LocalTask {
  format?: "docx" | "xlsx" | "ocr" | "pptx";
  id: string;
  instruction: string;
  agent_id?: string;
  source: string;
  output: string;
  operation?: Operation;
  status: string;
  error?: string;
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

export default function LocalDocxPanel({ deviceId }: { deviceId: string }) {
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
  const [format, setFormat] = useState<"docx" | "xlsx" | "ocr" | "pptx">(
    "docx",
  );
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
  }[format];
  const [sourceGrant, setSourceGrant] = useState("");
  const [outputGrant, setOutputGrant] = useState("");
  const [source, setSource] = useState("");
  const [output, setOutput] = useState("");
  const [instruction, setInstruction] = useState("");

  useEffect(() => {
    setState({ device_id: deviceId, grants: [], tasks: [], busy: false });
    setFormat("docx");
    setOperation("run");
    setLastRequest(null);
    setExpertId("");
    setExperts([]);
    setExpertError(null);
    let active = true;
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
    send({ action: "list" });
    return () => {
      active = false;
      window.removeEventListener("colink:docx-status", receive);
    };
  }, [deviceId]);

  const dispatch = (command: Record<string, string>) => {
    setState((previous) => ({ ...previous, busy: true, error: undefined }));
    try {
      send(format !== "docx" ? { ...command, format } : command);
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
    border: `1px solid ${token.colorBorder}`,
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
    interrupted: t("localDocx.interrupted", "已中断"),
  };
  const operations: Record<Operation, string> = {
    run: t("localDocx.createOperation", "生成新文件"),
    overwrite: t("localDocx.overwriteOperation", "覆盖现有文件"),
    delete: t("localDocx.deleteOperation", "删除文件"),
  };
  const errorMessage = (message: string) =>
    /^OCR_[A-Z_]+$/.test(message)
      ? t(`localOcr.errors.${message}`, t("localOcr.errors.OCR_INTERNAL"))
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
        <div>
          <label htmlFor={`${id}-format`}>
            {t("localDocx.format", "文件类型")}
          </label>
          <select
            id={`${id}-format`}
            value={format}
            disabled={state.busy}
            style={selectStyle}
            onChange={(event) => {
              setFormat(event.target.value as "docx" | "xlsx" | "ocr" | "pptx");
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
              disabled={state.busy}
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
                disabled={state.busy}
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
                : "支持授权目录直属的 DOCX 文件（不含子目录），文件不超过 20 MB、正文不超过 60,000 字符；生成基础正文，不保留原版式。",
            )}
          </Typography.Text>
        )}
        <Space wrap>
          <Button
            icon={<FolderPlus size={16} />}
            disabled={state.busy}
            onClick={() => dispatch({ action: "grant", mode: "ro" })}
          >
            {t("localDocx.grantRead", "选择只读目录")}
          </Button>
          <Button
            icon={<FolderPlus size={16} />}
            disabled={state.busy}
            onClick={() => dispatch({ action: "grant", mode: "rw" })}
          >
            {t("localDocx.grantWrite", "选择读写目录")}
          </Button>
          {format !== "ocr" && state.managed_directory && (
            <Button
              icon={<FolderPlus size={16} />}
              disabled={state.busy}
              onClick={() => dispatch({ action: "grant_managed" })}
            >
              {t("localDocx.grantManaged", "添加受管目录")}
            </Button>
          )}
          <Button
            disabled={state.busy}
            onClick={() => dispatch({ action: "list" })}
          >
            {t("localDocx.refresh", "刷新本机记录")}
          </Button>
        </Space>
        {state.grants.length > 0 && (
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            {state.grants.map((grant) => (
              <div key={grant.id}>
                <Tag color={grant.mode === "rw" ? "blue" : "default"}>
                  {grant.mode === "rw"
                    ? t("localDocx.rw", "读写")
                    : t("localDocx.ro", "只读")}
                </Tag>
                <Typography.Text style={{ overflowWrap: "anywhere" }}>
                  {grant.path}
                </Typography.Text>
              </div>
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
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submitTask();
          }}
        >
          <fieldset
            disabled={state.busy}
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
                  <Tag
                    color={
                      task.status === "completed"
                        ? "success"
                        : task.status === "failed"
                        ? "error"
                        : "processing"
                    }
                  >
                    {statuses[task.status] ?? task.status}
                  </Tag>
                  <Tag>{(task.format ?? "docx").toUpperCase()}</Tag>
                  {task.operation && task.operation !== "run" && (
                    <Tag>{operations[task.operation]}</Tag>
                  )}
                  <Typography.Text strong>{task.instruction}</Typography.Text>
                  <div>
                    <Typography.Text type="secondary">
                      {task.source}
                    </Typography.Text>
                  </div>
                  <Typography.Text style={{ overflowWrap: "anywhere" }}>
                    {task.output}
                  </Typography.Text>
                  {task.error && (
                    <Alert type="error" message={errorMessage(task.error)} />
                  )}
                </div>
              ))}
            </Space>
          </section>
        )}
      </Space>
    </Card>
  );
}
