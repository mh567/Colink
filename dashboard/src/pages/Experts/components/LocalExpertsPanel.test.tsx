import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import LocalExpertsPanel from "./LocalExpertsPanel";
import { localExpertsApi } from "../../../api/modules/localExperts";

vi.mock("../../../utils/saveBlobAsFile", () => ({ saveBlobAsFile: vi.fn() }));

const identity = vi.hoisted(() => ({ role: "user", desktop: false }));
vi.mock("../../../utils/desktopChrome", () => ({
  isDesktopShell: () => identity.desktop,
}));
vi.mock("../../../utils/copyText", () => ({ copyText: vi.fn() }));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => identity,
}));
vi.mock("../../../api/modules/localExperts", () => ({
  localExpertsApi: {
    list: vi.fn(),
    resources: vi.fn(),
    releases: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    install: vi.fn(),
    preview: vi.fn(),
    publish: vi.fn(),
    export: vi.fn(),
  },
}));
beforeEach(() => {
  vi.clearAllMocks();
  identity.role = "user";
  identity.desktop = false;
  vi.mocked(localExpertsApi.list).mockResolvedValue([]);
  vi.mocked(localExpertsApi.releases).mockResolvedValue([]);
  vi.mocked(localExpertsApi.resources).mockResolvedValue({
    models: [{ id: "intranet/model", name: "企业模型" }],
    actions: [{ id: "local_docx", name: "DOCX" }],
    skills: [],
    knowledge_bases: [],
  });
});
it("creates a private expert with selected resources and no publishing control for employees", async () => {
  render(
    <MemoryRouter>
      <LocalExpertsPanel />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "新建私人专家" }));
  await user.type(screen.getByLabelText("专家名称"), "会议助手");
  await user.type(screen.getByLabelText("专家说明"), "整理会议待办");
  await user.selectOptions(screen.getByLabelText("模型"), "intranet/model");
  await user.click(screen.getByLabelText("DOCX"));
  await user.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() =>
    expect(localExpertsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "会议助手",
        system_prompt: "整理会议待办",
        default_model: "intranet/model",
        actions: ["local_docx"],
        skill_package_ids: [],
        knowledge_base_ids: [],
      }),
    ),
  );
  expect(screen.queryByLabelText("导入离线包")).not.toBeInTheDocument();
});
it("shows loading errors and does not offer empty authorization choices as success", async () => {
  vi.mocked(localExpertsApi.resources).mockRejectedValue(
    new Error("资源不可用"),
  );
  render(
    <MemoryRouter>
      <LocalExpertsPanel />
    </MemoryRouter>,
  );
  expect(await screen.findByText("资源不可用")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "新建私人专家" })).toBeDisabled();
});

it("requires explicit review before publishing the uploaded package", async () => {
  identity.role = "admin";
  const pack = {
    name: "Reviewed",
    description: "Office",
    version: "1.0",
    source: "Internal",
    license: "MIT",
    system_prompt: "整理表格",
    actions: ["local_docx"],
    skills: [
      { name: "sum", content: "核对合计", source: "Internal", license: "MIT" },
    ],
  };
  vi.mocked(localExpertsApi.preview).mockResolvedValue(pack);
  render(
    <MemoryRouter>
      <LocalExpertsPanel />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  const file = new File([JSON.stringify(pack)], "expert.json", {
    type: "application/json",
  });
  Object.defineProperty(file, "text", {
    value: () => Promise.resolve(JSON.stringify(pack)),
  });
  await user.upload(screen.getByLabelText("导入离线包"), file);
  const publish = await screen.findByRole("button", { name: "审核通过并发布" });
  expect(publish).toBeDisabled();
  expect(screen.getByText(/核对合计/)).toBeInTheDocument();
  expect(localExpertsApi.publish).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("已核对来源、许可、内容及权限"));
  await user.click(publish);
  await waitFor(() =>
    expect(localExpertsApi.publish).toHaveBeenCalledWith(pack),
  );
});

it("installs the chosen approved version with the employee's model selection", async () => {
  vi.mocked(localExpertsApi.releases).mockResolvedValue([
    {
      id: "version-2",
      name: "季度助手",
      description: "",
      version: "2",
      source: "Internal",
      license: "MIT",
      system_prompt: "整理季度材料",
      actions: ["local_docx"],
      skills: [],
    },
  ]);
  render(
    <MemoryRouter>
      <LocalExpertsPanel />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "选择此版本" }));
  expect(screen.getByLabelText("专家说明")).toHaveAttribute("readonly");
  await user.type(screen.getByLabelText("专家说明"), "新增内容");
  expect(screen.getByLabelText("专家说明")).toHaveValue("整理季度材料");
  await user.selectOptions(screen.getByLabelText("模型"), "intranet/model");
  await user.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() =>
    expect(localExpertsApi.install).toHaveBeenCalledWith(
      "version-2",
      expect.objectContaining({
        default_model: "intranet/model",
        actions: ["local_docx"],
        knowledge_base_ids: [],
      }),
    ),
  );
});
it("exports only through the checked package endpoint and the existing download helper", async () => {
  const { saveBlobAsFile } = await import("../../../utils/saveBlobAsFile");
  vi.mocked(localExpertsApi.list).mockResolvedValue([
    {
      agent_id: "mine",
      name: "会议助手",
      description: "",
      system_prompt: "整理",
      default_model: "intranet/model",
      actions: ["local_docx"],
      skill_package_ids: [],
      knowledge_base_ids: [],
      published_expert_id: null,
    },
  ]);
  vi.mocked(localExpertsApi.export).mockResolvedValue({
    name: "会议助手",
    description: "",
    version: "1",
    source: "自有",
    license: "MIT",
    system_prompt: "整理",
    actions: ["local_docx"],
    skills: [],
  });
  render(
    <MemoryRouter>
      <LocalExpertsPanel />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "导出离线包" }));
  await user.type(screen.getByLabelText("版本号"), "1");
  await user.type(screen.getByLabelText("来源"), "自有");
  await user.type(screen.getByLabelText("许可"), "MIT");
  await user.click(screen.getByRole("button", { name: "下载离线包" }));
  await waitFor(() =>
    expect(saveBlobAsFile).toHaveBeenCalledWith(
      expect.any(Blob),
      "expert.json",
    ),
  );
  expect(localExpertsApi.export).toHaveBeenCalledWith("mine", {
    version: "1",
    source: "自有",
    license: "MIT",
  });
});

it("offers complete JSON for desktop export and reports clipboard failure", async () => {
  identity.desktop = true;
  const { saveBlobAsFile } = await import("../../../utils/saveBlobAsFile");
  const { copyText } = await import("../../../utils/copyText");
  vi.mocked(copyText).mockResolvedValue(false);
  const pack = {
    name: "会议助手",
    description: "",
    version: "1",
    source: "自有",
    license: "MIT",
    system_prompt: "整理",
    actions: ["local_docx"],
    skills: [],
  };
  vi.mocked(localExpertsApi.list).mockResolvedValue([
    {
      agent_id: "mine",
      name: "会议助手",
      description: "",
      system_prompt: "整理",
      default_model: "intranet/model",
      actions: ["local_docx"],
      skill_package_ids: [],
      knowledge_base_ids: [],
      published_expert_id: null,
    },
  ]);
  vi.mocked(localExpertsApi.export).mockResolvedValue(pack);
  render(
    <MemoryRouter>
      <LocalExpertsPanel />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "导出离线包" }));
  await user.type(screen.getByLabelText("版本号"), "1");
  await user.type(screen.getByLabelText("来源"), "自有");
  await user.type(screen.getByLabelText("许可"), "MIT");
  await user.click(screen.getByRole("button", { name: "生成离线包文本" }));
  expect(await screen.findByLabelText("离线包 JSON 文本")).toHaveValue(
    JSON.stringify(pack, null, 2),
  );
  expect(saveBlobAsFile).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "复制离线包文本" }));
  expect(
    await screen.findByText("复制失败，请手动选择并复制下方完整文本。"),
  ).toBeInTheDocument();
  expect(copyText).toHaveBeenCalledWith(JSON.stringify(pack, null, 2));
});
