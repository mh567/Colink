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

interface Grant {
  id: string;
  path: string;
  mode: "ro" | "rw";
}
interface LocalTask {
  format?: "docx" | "xlsx";
  id: string;
  instruction: string;
  source: string;
  output: string;
  status: string;
  error?: string;
}
interface NativeState {
  device_id: string;
  grants: Grant[];
  tasks: LocalTask[];
  busy: boolean;
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
  const [format, setFormat] = useState<"docx" | "xlsx">("docx");
  const contentKey = format === "xlsx" ? "localXlsx" : "localDocx";
  const [sourceGrant, setSourceGrant] = useState("");
  const [outputGrant, setOutputGrant] = useState("");
  const [source, setSource] = useState("");
  const [output, setOutput] = useState("");
  const [instruction, setInstruction] = useState("");

  useEffect(() => {
    setState({ device_id: deviceId, grants: [], tasks: [], busy: false });
    setFormat("docx");
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
    return () => window.removeEventListener("colink:docx-status", receive);
  }, [deviceId]);

  const dispatch = (command: Record<string, string>) => {
    setState((previous) => ({ ...previous, busy: true, error: undefined }));
    try {
      send(format === "xlsx" ? { ...command, format } : command);
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
    completed: t("localDocx.completed", "已完成"),
    failed: t("localDocx.failed", "失败"),
    interrupted: t("localDocx.interrupted", "已中断"),
  };
  const canRun =
    state.grants.some((grant) => grant.id === sourceGrant) &&
    state.grants.some(
      (grant) => grant.id === outputGrant && grant.mode === "rw",
    ) &&
    source.trim() &&
    output.trim() &&
    instruction.trim();

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
              setFormat(event.target.value as "docx" | "xlsx");
              setSource("");
              setOutput("");
              setInstruction("");
            }}
          >
            <option value="docx">DOCX</option>
            <option value="xlsx">XLSX</option>
          </select>
        </div>
        <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
          {t(
            `${contentKey}.privacy`,
            "原始文件保留在本机；所选文档的正文经服务端发送到已配置模型。成果写入授权目录，不覆盖已有文件。",
          )}
        </Typography.Paragraph>
        <Typography.Text type="secondary">
          {t(
            `${contentKey}.limits`,
            "支持授权目录直属的 DOCX 文件（不含子目录），文件不超过 20 MB、正文不超过 60,000 字符；生成基础正文，不保留原版式。",
          )}
        </Typography.Text>
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
        {state.error && <Alert type="error" showIcon message={state.error} />}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (canRun && !state.busy)
              dispatch({
                action: "run",
                source_grant: sourceGrant,
                source: source.trim(),
                output_grant: outputGrant,
                output: output.trim(),
                instruction: instruction.trim(),
              });
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
              <div>
                <label htmlFor={`${id}-output-grant`}>
                  {t("localDocx.outputGrant", "成果目录")}
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
                  {state.grants
                    .filter((grant) => grant.mode === "rw")
                    .map((grant) => (
                      <option key={grant.id} value={grant.id}>
                        {grant.path}
                      </option>
                    ))}
                </select>
              </div>
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
                    "例如：会议记录.docx",
                  )}
                  maxLength={240}
                  required
                />
              </div>
              <div>
                <label htmlFor={`${id}-output`}>
                  {t("localDocx.output", "新文件名")}
                </label>
                <Input
                  id={`${id}-output`}
                  value={output}
                  onChange={(event) => setOutput(event.target.value)}
                  placeholder={t(
                    `${contentKey}.outputExample`,
                    "例如：会议纪要.docx",
                  )}
                  maxLength={240}
                  required
                />
              </div>
            </div>
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
            <Button
              type="primary"
              htmlType="submit"
              loading={state.busy}
              disabled={!canRun}
            >
              {t(
                `${contentKey}.run`,
                format === "xlsx" ? "生成 XLSX" : "生成 DOCX",
              )}
            </Button>
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
                  <Typography.Text strong>{task.instruction}</Typography.Text>
                  <div>
                    <Typography.Text type="secondary">
                      {task.source}
                    </Typography.Text>
                  </div>
                  <Typography.Text style={{ overflowWrap: "anywhere" }}>
                    {task.output}
                  </Typography.Text>
                  {task.error && <Alert type="error" message={task.error} />}
                </div>
              ))}
            </Space>
          </section>
        )}
      </Space>
    </Card>
  );
}
