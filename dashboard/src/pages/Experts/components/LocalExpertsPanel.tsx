import { useCallback, useEffect, useId, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Drawer,
  Empty,
  Input,
  Space,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import {
  localExpertsApi,
  type LocalExpert,
  type LocalExpertInput,
  type LocalExpertRelease,
  type LocalExpertResources,
  type OfflineExpertPackage,
} from "../../../api/modules/localExperts";
import { apiErrorMessage } from "../../../utils/apiError";
import { saveBlobAsFile } from "../../../utils/saveBlobAsFile";
import { isDesktopShell } from "../../../utils/desktopChrome";
import { copyText } from "../../../utils/copyText";

const blank: LocalExpertInput = {
  name: "",
  description: "",
  system_prompt: "",
  default_model: "",
  actions: [],
  skill_package_ids: [],
  knowledge_base_ids: [],
};

export default function LocalExpertsPanel() {
  const { t } = useTranslation();
  const currentUser = useCurrentUser();
  const navigate = useNavigate();
  const id = useId();
  const [experts, setExperts] = useState<LocalExpert[]>([]);
  const [releases, setReleases] = useState<LocalExpertRelease[]>([]);
  const [resources, setResources] = useState<LocalExpertResources | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<LocalExpertInput | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);
  const desktop = isDesktopShell();
  const [exportText, setExportText] = useState("");
  const [copied, setCopied] = useState(false);
  const [exporting, setExporting] = useState<LocalExpert | null>(null);
  const [exportMeta, setExportMeta] = useState({
    version: "",
    source: "",
    license: "",
  });
  const [preview, setPreview] = useState<OfflineExpertPackage | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [success, setSuccess] = useState(false);

  const load = useCallback(async () => {
    const [items, versions, choices] = await Promise.all([
      localExpertsApi.list(),
      localExpertsApi.releases(),
      localExpertsApi.resources(),
    ]);
    setExperts(items);
    setReleases(versions);
    setResources(choices);
  }, []);
  useEffect(() => {
    let active = true;
    void load().catch((err: unknown) => {
      if (active) setError(err);
    });
    return () => {
      active = false;
    };
  }, [load]);

  const perform = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setSuccess(false);
    try {
      await action();
      setSuccess(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  const openCreate = (expert?: LocalExpert, release?: LocalExpertRelease) => {
    setEditing(expert?.agent_id ?? null);
    setInstalling(release?.id ?? null);
    setError(null);
    setSuccess(false);
    setForm(
      expert
        ? {
            name: expert.name,
            description: expert.description,
            system_prompt: expert.system_prompt,
            default_model: expert.default_model,
            actions: [...expert.actions],
            skill_package_ids: [...expert.skill_package_ids],
            knowledge_base_ids: [...expert.knowledge_base_ids],
          }
        : {
            ...blank,
            ...(release
              ? {
                  name: release.name,
                  description: release.description,
                  system_prompt: release.system_prompt,
                  actions: release.actions.filter(
                    (action) =>
                      resources?.actions.some((choice) => choice.id === action),
                  ),
                }
              : {}),
          },
    );
  };
  const save = () =>
    perform(async () => {
      if (!form) return;
      if (editing) await localExpertsApi.update(editing, form);
      else if (installing) await localExpertsApi.install(installing, form);
      else await localExpertsApi.create(form);
      setForm(null);
      await load();
    });
  const toggle = (
    field: "actions" | "skill_package_ids" | "knowledge_base_ids",
    value: string,
    checked: boolean,
  ) => {
    setForm((previous) =>
      previous
        ? {
            ...previous,
            [field]: checked
              ? [...previous[field], value]
              : previous[field].filter((item) => item !== value),
          }
        : null,
    );
  };
  const importPackage = (file: File) =>
    perform(async () => {
      setPreview(null);
      setReviewed(false);
      if (file.size > 1024 * 1024)
        throw new Error(
          t("localExperts.packageTooLarge", "离线包不能超过 1 MiB。"),
        );
      setPreview(await localExpertsApi.preview(JSON.parse(await file.text())));
    });
  const exportPackage = () =>
    perform(async () => {
      if (!exporting) return;
      const pack = await localExpertsApi.export(exporting.agent_id, exportMeta);
      const text = JSON.stringify(pack, null, 2);
      if (desktop) {
        setExportText(text);
        setCopied(false);
      } else {
        await saveBlobAsFile(
          new Blob([text], { type: "application/json" }),
          "expert.json",
        );
        setExporting(null);
      }
    });
  const selectStyle = {
    width: "100%",
    padding: 8,
    background: "var(--ant-color-bg-container)",
    color: "inherit",
    border: "1px solid currentColor",
    borderRadius: 6,
  };
  const field = (
    key: "name" | "description" | "system_prompt",
    label: string,
    rows = 1,
  ) => (
    <div>
      <label htmlFor={`${id}-${key}`}>{label}</label>
      {rows > 1 ? (
        <Input.TextArea
          id={`${id}-${key}`}
          rows={rows}
          readOnly={key === "system_prompt" && !!installing}
          value={form?.[key]}
          required={key !== "description"}
          onChange={(event) =>
            setForm((previous) =>
              previous ? { ...previous, [key]: event.target.value } : null,
            )
          }
        />
      ) : (
        <Input
          id={`${id}-${key}`}
          value={form?.[key]}
          required
          onChange={(event) =>
            setForm((previous) =>
              previous ? { ...previous, [key]: event.target.value } : null,
            )
          }
        />
      )}
    </div>
  );
  const errorNotice = error ? (
    <Alert
      type="error"
      showIcon
      message={apiErrorMessage(
        error,
        t("localExperts.failed", "操作失败，请重试。"),
        t,
      )}
    />
  ) : null;

  return (
    <Space
      direction="vertical"
      size={20}
      style={{ width: "100%", padding: 16 }}
    >
      <Alert
        type="info"
        showIcon
        message={t(
          "localExperts.hint",
          "私人专家只使用本人已授权资源。技能作为任务说明使用；本机文件仍受目录、设备和动作授权约束。",
        )}
      />
      {!form && !exporting && errorNotice}
      {success && (
        <Alert type="success" message={t("localExperts.saved", "操作成功")} />
      )}
      <Space wrap>
        <Button
          type="primary"
          disabled={!resources || busy}
          onClick={() => openCreate()}
        >
          {t("localExperts.create", "新建私人专家")}
        </Button>
        <Button disabled={busy} onClick={() => void perform(load)}>
          {t("localExperts.refresh", "刷新")}
        </Button>
        <Button onClick={() => navigate("/devices")}>
          {t("localExperts.run", "前往本机任务")}
        </Button>
      </Space>
      <Typography.Title level={4}>
        {t("localExperts.mine", "我的私人专家")}
      </Typography.Title>
      {experts.length === 0 && (
        <Empty description={t("localExperts.empty", "尚无私人专家")} />
      )}
      {experts.map((expert) => (
        <Card
          key={expert.agent_id}
          title={expert.name}
          size="small"
          extra={
            <Space>
              <Button disabled={busy} onClick={() => openCreate(expert)}>
                {t("localExperts.edit", "编辑")}
              </Button>
              <Button
                disabled={busy}
                onClick={() => {
                  setExporting(expert);
                  setExportText("");
                  setCopied(false);
                  setExportMeta({ version: "", source: "", license: "" });
                  setError(null);
                }}
              >
                {t("localExperts.export", "导出离线包")}
              </Button>
            </Space>
          }
        >
          <p>{expert.description}</p>
          <Tag>{expert.default_model}</Tag>
          {expert.actions.map((action) => (
            <Tag key={action}>{action === "local_docx" ? "DOCX" : "XLSX"}</Tag>
          ))}
          <Typography.Text type="secondary">
            {expert.published_expert_id
              ? t("localExperts.installed", "来自批准版本，按本人权限执行")
              : t("localExperts.private", "仅本人可用，未发布")}
          </Typography.Text>
        </Card>
      ))}
      <Typography.Title level={4}>
        {t("localExperts.releases", "企业批准版本")}
      </Typography.Title>
      {releases.length === 0 && (
        <Empty description={t("localExperts.noReleases", "暂无批准版本")} />
      )}
      {releases.map((release) => (
        <Card
          key={release.id}
          size="small"
          title={`${release.name} · ${release.version}`}
          extra={
            <Button
              disabled={!resources || busy}
              onClick={() => openCreate(undefined, release)}
            >
              {t("localExperts.install", "选择此版本")}
            </Button>
          }
        >
          <p>{release.description}</p>
          <p>
            {t("localExperts.source", "来源")}: {release.source}
          </p>
          <p>
            {t("localExperts.license", "许可")}: {release.license}
          </p>
        </Card>
      ))}
      {currentUser?.role === "admin" && (
        <Card title={t("localExperts.reviewTitle", "审核离线包并发布")}>
          <Space direction="vertical" style={{ width: "100%" }}>
            <Alert
              type="info"
              message={t(
                "localExperts.reviewHint",
                "核对来源、许可、全部内容及动作范围。更新使用新的版本号，原批准版本保留；离线包不得包含私人资料或凭据。",
              )}
            />
            <label htmlFor={`${id}-import`}>
              {t("localExperts.import", "导入离线包")}
            </label>
            <input
              id={`${id}-import`}
              type="file"
              accept=".json,application/json"
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void importPackage(file);
                event.target.value = "";
              }}
            />
            {preview && (
              <>
                <Typography.Title level={5}>
                  {preview.name} · {preview.version}
                </Typography.Title>
                <Typography.Paragraph>
                  {preview.description}
                </Typography.Paragraph>
                <p>
                  {t("localExperts.source", "来源")}: {preview.source}
                </p>
                <p>
                  {t("localExperts.license", "许可")}: {preview.license}
                </p>
                <p>
                  {t("localExperts.actions", "本机动作")}:{" "}
                  {preview.actions
                    .map((action) =>
                      action === "local_docx" ? "DOCX" : "XLSX",
                    )
                    .join(", ")}
                </p>
                <Typography.Title level={5}>
                  {t("localExperts.prompt", "专家说明")}
                </Typography.Title>
                <pre
                  style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                >
                  {preview.system_prompt}
                </pre>
                {preview.skills.map((skill, index) => (
                  <Card key={index} size="small" title={skill.name}>
                    <p>
                      {t("localExperts.source", "来源")}: {skill.source}
                    </p>
                    <p>
                      {t("localExperts.license", "许可")}: {skill.license}
                    </p>
                    <pre
                      style={{
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                      }}
                    >
                      {skill.content}
                    </pre>
                  </Card>
                ))}
                <Checkbox
                  checked={reviewed}
                  onChange={(event) => setReviewed(event.target.checked)}
                >
                  {t("localExperts.reviewed", "已核对来源、许可、内容及权限")}
                </Checkbox>
                <Button
                  type="primary"
                  disabled={!reviewed}
                  loading={busy}
                  onClick={() =>
                    void perform(async () => {
                      await localExpertsApi.publish(preview);
                      setPreview(null);
                      setReviewed(false);
                      await load();
                    })
                  }
                >
                  {t("localExperts.publish", "审核通过并发布")}
                </Button>
              </>
            )}
          </Space>
        </Card>
      )}
      <Drawer
        title={
          installing
            ? t("localExperts.install", "选择此版本")
            : editing
            ? t("localExperts.edit", "编辑")
            : t("localExperts.create", "新建私人专家")
        }
        open={!!form}
        width={520}
        onClose={() => {
          if (!busy) setForm(null);
        }}
        destroyOnHidden
      >
        {form && (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              {errorNotice}
              {field("name", t("localExperts.name", "专家名称"))}
              {field("description", t("localExperts.description", "简介"), 2)}
              {field("system_prompt", t("localExperts.prompt", "专家说明"), 5)}
              <div>
                <label htmlFor={`${id}-model`}>
                  {t("localExperts.model", "模型")}
                </label>
                <select
                  id={`${id}-model`}
                  style={selectStyle}
                  required
                  value={form.default_model}
                  onChange={(event) =>
                    setForm({ ...form, default_model: event.target.value })
                  }
                >
                  <option value="">
                    {t("localExperts.chooseModel", "请选择已授权模型")}
                  </option>
                  {resources?.models.map((model) => (
                    <option key={model.id} value={model.id}>
                      {model.name}
                    </option>
                  ))}
                </select>
              </div>
              {(
                [
                  ["actions", "actions", t("localExperts.actions", "本机动作")],
                  [
                    "skill_package_ids",
                    "skills",
                    t("localExperts.skills", "批准的技能说明"),
                  ],
                  [
                    "knowledge_base_ids",
                    "knowledge_bases",
                    t("localExperts.knowledge", "资料库"),
                  ],
                ] as const
              ).map(([fieldName, choices, label]) => (
                <fieldset key={fieldName} style={{ border: 0, padding: 0 }}>
                  <legend>{label}</legend>
                  <Space direction="vertical">
                    {resources?.[choices].map((choice) => (
                      <Checkbox
                        key={choice.id}
                        checked={form[fieldName].includes(choice.id)}
                        onChange={(event) =>
                          toggle(fieldName, choice.id, event.target.checked)
                        }
                      >
                        {choice.name}
                      </Checkbox>
                    ))}
                  </Space>
                </fieldset>
              ))}
              <Button
                type="primary"
                htmlType="submit"
                loading={busy}
                disabled={!form.actions.length}
              >
                {t("localExperts.save", "保存")}
              </Button>
            </Space>
          </form>
        )}
      </Drawer>
      <Drawer
        title={t("localExperts.export", "导出离线包")}
        open={!!exporting}
        width={480}
        onClose={() => {
          if (!busy) setExporting(null);
        }}
        destroyOnHidden
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void exportPackage();
          }}
        >
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            {errorNotice}
            <Alert
              type="info"
              message={t(
                "localExperts.exportHint",
                "导出前请移除私人资源引用，并检查专家说明中没有私人资料或凭据。导出不会自动发布。",
              )}
            />
            {(
              [
                ["version", t("localExperts.version", "版本号")],
                ["source", t("localExperts.source", "来源")],
                ["license", t("localExperts.license", "许可")],
              ] as const
            ).map(([key, label]) => (
              <div key={key}>
                <label htmlFor={`${id}-export-${key}`}>{label}</label>
                <Input
                  id={`${id}-export-${key}`}
                  value={exportMeta[key]}
                  required
                  onChange={(event) => {
                    setExportMeta({ ...exportMeta, [key]: event.target.value });
                    setExportText("");
                    setCopied(false);
                  }}
                />
              </div>
            ))}
            {desktop && (
              <Alert
                type="info"
                message={t(
                  "localExperts.desktopExport",
                  "桌面端请复制完整 JSON 文本，保存为 .json 文件后交给管理员审核。此包仅含任务说明，不执行脚本。",
                )}
              />
            )}
            {exportText && (
              <>
                <label htmlFor={`${id}-export-text`}>
                  {t("localExperts.packageText", "离线包 JSON 文本")}
                </label>
                <Input.TextArea
                  id={`${id}-export-text`}
                  value={exportText}
                  readOnly
                  rows={14}
                />
                <Button
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      setCopied(false);
                      if (!(await copyText(exportText)))
                        throw new Error(
                          t(
                            "localExperts.copyFailed",
                            "复制失败，请手动选择并复制下方完整文本。",
                          ),
                        );
                      setCopied(true);
                    })
                  }
                >
                  {t("localExperts.copyPackage", "复制离线包文本")}
                </Button>
                {copied && (
                  <Alert
                    type="success"
                    message={t(
                      "localExperts.copied",
                      "已复制，请保存为 .json 文件后交给管理员审核。",
                    )}
                  />
                )}
              </>
            )}
            <Button type="primary" htmlType="submit" loading={busy}>
              {desktop
                ? t("localExperts.generateText", "生成离线包文本")
                : t("localExperts.download", "下载离线包")}
            </Button>
          </Space>
        </form>
      </Drawer>
    </Space>
  );
}
