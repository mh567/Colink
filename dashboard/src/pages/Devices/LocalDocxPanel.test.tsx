import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import LocalDocxPanel from "./LocalDocxPanel";
vi.mock("../../api/modules/localExperts", () => ({
  localExpertsApi: { list: vi.fn().mockResolvedValue([]) },
}));

afterEach(() => {
  delete (window as Window & { _wails?: unknown })._wails;
});

it("submits selected local paths and displays the native result", async () => {
  const invoke = vi.fn();
  (window as Window & { _wails?: unknown })._wails = { invoke };
  render(<LocalDocxPanel deviceId="mine" />);
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: false,
          grants: [
            { id: "ro", path: "/资料", mode: "ro" },
            { id: "rw", path: "/成果", mode: "rw" },
          ],
          tasks: [],
        },
      }),
    );
  });
  expect(screen.getByText(/原始文件保留在本机/)).toBeInTheDocument();
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("资料目录"), "ro");
  await user.selectOptions(screen.getByLabelText("成果目录"), "rw");
  expect(
    screen.getByLabelText("成果目录").querySelector('option[value="ro"]'),
  ).toBeNull();
  await user.type(screen.getByLabelText("源文件名"), "会议.docx");
  await user.type(screen.getByLabelText("新文件名"), "纪要.docx");
  await user.type(screen.getByLabelText("任务描述"), "整理会议待办");
  await user.click(screen.getByRole("button", { name: "生成 DOCX" }));
  const command = JSON.parse(
    decodeURIComponent(invoke.mock.calls.at(-1)![0].split("desktop:docx:")[1]),
  );
  expect(command).toEqual({
    action: "run",
    source_grant: "ro",
    source: "会议.docx",
    output_grant: "rw",
    output: "纪要.docx",
    instruction: "整理会议待办",
  });
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: false,
          grants: [],
          tasks: [
            {
              id: "1",
              instruction: "整理会议待办",
              source: "会议.docx",
              output: "/成果/纪要.docx",
              status: "completed",
            },
          ],
        },
      }),
    );
  });
  expect(screen.getByText("/成果/纪要.docx")).toBeInTheDocument();
  expect(screen.getByText("已完成")).toBeInTheDocument();
});

it("ignores another device and displays native rejection", async () => {
  (window as Window & { _wails?: unknown })._wails = { invoke: vi.fn() };
  render(<LocalDocxPanel deviceId="mine" />);
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "other",
          busy: false,
          grants: [{ id: "secret", path: "private", mode: "rw" }],
          tasks: [],
        },
      }),
    );
  });
  expect(screen.queryByText(/private/)).toBeNull();
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: false,
          grants: [],
          tasks: [],
          error: "目录只读，拒绝写入",
        },
      }),
    );
  });
  expect(screen.getByText("目录只读，拒绝写入")).toBeInTheDocument();
});

it.each(["completed", "failed"])(
  "submits XLSX tasks and displays native %s",
  async (status) => {
    const invoke = vi.fn();
    (window as Window & { _wails?: unknown })._wails = { invoke };
    render(<LocalDocxPanel deviceId="mine" />);
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("colink:docx-status", {
          detail: {
            device_id: "mine",
            busy: false,
            grants: [
              { id: "ro", path: "/资料", mode: "ro" },
              { id: "rw", path: "/成果", mode: "rw" },
            ],
            tasks: [],
          },
        }),
      );
    });
    expect(screen.getByText(/原始文件保留在本机/)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("文件类型"), "xlsx");
    await user.selectOptions(screen.getByLabelText("资料目录"), "ro");
    await user.selectOptions(screen.getByLabelText("成果目录"), "rw");
    expect(
      screen.getByLabelText("成果目录").querySelector('option[value="ro"]'),
    ).toBeNull();
    await user.type(screen.getByLabelText("源文件名"), "会议.xlsx");
    await user.type(screen.getByLabelText("新文件名"), "纪要.xlsx");
    await user.type(screen.getByLabelText("任务描述"), "整理会议待办");
    await user.click(screen.getByRole("button", { name: "生成 XLSX" }));
    const command = JSON.parse(
      decodeURIComponent(
        invoke.mock.calls.at(-1)![0].split("desktop:docx:")[1],
      ),
    );
    expect(command).toEqual({
      action: "run",
      format: "xlsx",
      source_grant: "ro",
      source: "会议.xlsx",
      output_grant: "rw",
      output: "纪要.xlsx",
      instruction: "整理会议待办",
    });
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("colink:docx-status", {
          detail: {
            device_id: "mine",
            busy: false,
            grants: [],
            tasks: [
              {
                id: "1",
                instruction: "整理会议待办",
                source: "会议.xlsx",
                output: "/成果/纪要.xlsx",
                format: "xlsx",
                status,
                error: status === "failed" ? "公式计算失败" : undefined,
              },
            ],
          },
        }),
      );
    });
    expect(screen.getByText("/成果/纪要.xlsx")).toBeInTheDocument();
    if (status === "failed") {
      expect(screen.getByText("公式计算失败")).toBeInTheDocument();
      expect(screen.queryByText("已完成")).toBeNull();
    } else {
      expect(screen.getByText("已完成")).toBeInTheDocument();
    }
  },
);

it.each(["completed", "failed"])(
  "runs OCR locally without a model instruction and displays %s",
  async (status) => {
    const { localExpertsApi } = await import("../../api/modules/localExperts");
    vi.mocked(localExpertsApi.list).mockResolvedValueOnce([
      {
        agent_id: "mine-expert",
        name: "会议专家",
        description: "",
        system_prompt: "整理",
        default_model: "intranet/chat",
        actions: ["local_docx"],
        skill_package_ids: [],
        knowledge_base_ids: [],
        published_expert_id: null,
      },
    ]);
    const invoke = vi.fn();
    (window as Window & { _wails?: unknown })._wails = { invoke };
    render(<LocalDocxPanel deviceId="mine" />);
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("colink:docx-status", {
          detail: {
            device_id: "mine",
            busy: false,
            grants: [
              { id: "ro", path: "/资料", mode: "ro" },
              { id: "rw", path: "/成果", mode: "rw" },
            ],
            tasks: [],
          },
        }),
      );
    });
    const user = userEvent.setup();
    await screen.findByRole("option", { name: "会议专家" });
    await user.selectOptions(screen.getByLabelText("任务专家"), "mine-expert");
    await user.selectOptions(screen.getByLabelText("文件类型"), "ocr");
    expect(screen.queryByLabelText("任务专家")).toBeNull();
    expect(screen.queryByLabelText("任务描述")).toBeNull();
    expect(screen.getByText(/原件和识别文字均保留在本机/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("资料目录"), "ro");
    await user.selectOptions(screen.getByLabelText("成果目录"), "rw");
    await user.type(screen.getByLabelText("源文件名"), "扫描资料.pdf");
    await user.type(screen.getByLabelText("新文件名"), "识别文字.txt");
    await user.click(screen.getByRole("button", { name: "识别扫描 PDF" }));
    expect(
      JSON.parse(
        decodeURIComponent(
          invoke.mock.calls.at(-1)![0].split("desktop:docx:")[1],
        ),
      ),
    ).toEqual({
      action: "run",
      format: "ocr",
      source_grant: "ro",
      source: "扫描资料.pdf",
      output_grant: "rw",
      output: "识别文字.txt",
      instruction: "",
    });
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("colink:docx-status", {
          detail: {
            device_id: "mine",
            busy: false,
            grants: [],
            tasks: [
              {
                id: "ocr-1",
                format: "ocr",
                instruction: "",
                source: "扫描资料.pdf",
                output: "/成果/识别文字.txt",
                status,
                error: status === "failed" ? "离线识别资源缺失" : undefined,
              },
            ],
          },
        }),
      );
    });
    expect(screen.getByText("/成果/识别文字.txt")).toBeInTheDocument();
    expect(
      screen.getByText(status === "failed" ? "离线识别资源缺失" : "已完成"),
    ).toBeInTheDocument();
  },
);
it("passes the selected authorized expert to the native task", async () => {
  const { localExpertsApi } = await import("../../api/modules/localExperts");
  vi.mocked(localExpertsApi.list).mockResolvedValueOnce([
    {
      agent_id: "mine-expert",
      name: "会议专家",
      description: "",
      system_prompt: "整理",
      default_model: "intranet/chat",
      actions: ["local_docx"],
      skill_package_ids: [],
      knowledge_base_ids: [],
      published_expert_id: null,
    },
    {
      agent_id: "xlsx-only",
      name: "表格专家",
      description: "",
      system_prompt: "整理",
      default_model: "intranet/chat",
      actions: ["local_xlsx"],
      skill_package_ids: [],
      knowledge_base_ids: [],
      published_expert_id: null,
    },
  ]);
  const invoke = vi.fn();
  (window as Window & { _wails?: unknown })._wails = { invoke };
  render(<LocalDocxPanel deviceId="mine" />);
  await screen.findByRole("option", { name: "会议专家" });
  expect(screen.queryByRole("option", { name: "表格专家" })).toBeNull();
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: false,
          grants: [{ id: "rw", path: "/成果", mode: "rw" }],
          tasks: [],
        },
      }),
    );
  });
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("任务专家"), "mine-expert");
  await user.selectOptions(screen.getByLabelText("资料目录"), "rw");
  await user.selectOptions(screen.getByLabelText("成果目录"), "rw");
  await user.type(screen.getByLabelText("源文件名"), "source.docx");
  await user.type(screen.getByLabelText("新文件名"), "result.docx");
  await user.type(screen.getByLabelText("任务描述"), "整理");
  await user.click(screen.getByRole("button", { name: "生成 DOCX" }));
  const command = JSON.parse(
    decodeURIComponent(invoke.mock.calls.at(-1)![0].split("desktop:docx:")[1]),
  );
  expect(command.agent_id).toBe("mine-expert");
});

it.each([
  { operation: "overwrite", button: "请求覆盖" },
  { operation: "delete", button: "请求删除" },
])(
  "requests native $operation only for a destructive directory grant",
  async ({ operation, button }) => {
    const invoke = vi.fn();
    (window as Window & { _wails?: unknown })._wails = { invoke };
    render(<LocalDocxPanel deviceId="mine" />);
    const grants = [
      { id: "ro", path: "/资料", mode: "ro" },
      { id: "rw", path: "/成果", mode: "rw" },
      { id: "delete-only", path: "/普通授权", mode: "rw", destructive: true },
      {
        id: "managed",
        path: "/受管",
        mode: "rw",
        destructive: true,
        overwrite: true,
      },
    ];
    const nativeStatus = (extra: Record<string, unknown> = {}) =>
      act(async () => {
        window.dispatchEvent(
          new CustomEvent("colink:docx-status", {
            detail: {
              device_id: "mine",
              busy: false,
              grants,
              tasks: [],
              ...extra,
            },
          }),
        );
      });
    const lastCommand = () =>
      JSON.parse(
        decodeURIComponent(
          invoke.mock.calls.at(-1)![0].split("desktop:docx:")[1],
        ),
      );
    await nativeStatus({
      grants: grants.filter((grant) => grant.id !== "managed"),
    });
    expect(screen.queryByRole("option", { name: "覆盖现有文件" })).toBeNull();
    expect(
      screen.getByRole("option", { name: "删除文件" }),
    ).toBeInTheDocument();
    await nativeStatus();
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("文件操作"), operation);
    const targetGrant = screen.getByLabelText("目标目录");
    expect(targetGrant.querySelector('option[value="rw"]')).toBeNull();
    expect(targetGrant.querySelector('option[value="ro"]')).toBeNull();
    expect(
      Boolean(targetGrant.querySelector('option[value="delete-only"]')),
    ).toBe(operation === "delete");
    expect(screen.getByRole("button", { name: button })).toBeDisabled();
    await user.selectOptions(targetGrant, "managed");
    await user.type(screen.getByLabelText("目标文件名"), "纪要.docx");

    const source = {
      source_grant: "ro",
      source: "会议.docx",
      instruction: "更新会议待办",
    };
    if (operation === "overwrite") {
      await user.selectOptions(
        screen.getByLabelText("资料目录"),
        source.source_grant,
      );
      await user.type(screen.getByLabelText("源文件名"), source.source);
      await user.type(screen.getByLabelText("任务描述"), source.instruction);
    } else {
      expect(screen.queryByLabelText("源文件名")).toBeNull();
      expect(screen.queryByLabelText("任务描述")).toBeNull();
    }
    await user.click(screen.getByRole("button", { name: button }));
    const original = lastCommand();
    expect(original).toEqual({
      action: operation,
      output_grant: "managed",
      output: "纪要.docx",
      ...(operation === "overwrite" ? source : {}),
      request_id: expect.stringMatching(/^[0-9a-f]{32}$/),
    });

    const task = {
      id: original.request_id,
      operation,
      instruction: operation === "overwrite" ? source.instruction : "",
      source: operation === "overwrite" ? source.source : "",
      output: "/受管/纪要.docx",
    };
    await nativeStatus({
      busy: true,
      tasks: [{ ...task, status: "awaiting_confirmation" }],
    });
    expect(screen.getByText("等待确认")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新建本次任务" })).toBeNull();
    await nativeStatus({ tasks: [{ ...task, status: "cancelled" }] });
    expect(screen.getByText("已取消")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: button }));
    expect(lastCommand()).toEqual(original);

    await nativeStatus({ tasks: [{ ...task, status: "cancelled" }] });
    await user.click(screen.getByRole("button", { name: "新建本次任务" }));
    const fresh = lastCommand();
    expect(fresh).toEqual({
      ...original,
      request_id: expect.stringMatching(/^[0-9a-f]{32}$/),
    });
    expect(fresh.request_id).not.toBe(original.request_id);

    await nativeStatus();
    const filename = screen.getByLabelText("目标文件名");
    await user.clear(filename);
    await user.type(filename, "另一份纪要.docx");
    await user.click(screen.getByRole("button", { name: button }));
    expect(lastCommand()).toEqual({
      ...original,
      output: "另一份纪要.docx",
      request_id: expect.stringMatching(/^[0-9a-f]{32}$/),
    });
    expect(lastCommand().request_id).not.toBe(fresh.request_id);
  },
);

it("requests the native managed directory only when the platform exposes it", async () => {
  const invoke = vi.fn();
  (window as Window & { _wails?: unknown })._wails = { invoke };
  render(<LocalDocxPanel deviceId="mine" />);
  expect(screen.queryByRole("button", { name: "添加受管目录" })).toBeNull();
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: false,
          grants: [],
          tasks: [],
          managed_directory: true,
        },
      }),
    );
  });
  await userEvent.click(screen.getByRole("button", { name: "添加受管目录" }));
  expect(
    JSON.parse(
      decodeURIComponent(
        invoke.mock.calls.at(-1)![0].split("desktop:docx:")[1],
      ),
    ),
  ).toEqual({ action: "grant_managed" });
});

it("generates a new presentation and shows the saved result", async () => {
  const invoke = vi.fn();
  (window as Window & { _wails?: unknown })._wails = { invoke };
  render(<LocalDocxPanel deviceId="mine" />);
  const user = userEvent.setup();
  const grants = [
    { id: "ro", path: "/资料", mode: "ro" },
    { id: "rw", path: "/成果", mode: "rw", destructive: true, overwrite: true },
  ];
  const status = async (tasks: object[]) => {
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("colink:docx-status", {
          detail: { device_id: "mine", busy: false, grants, tasks },
        }),
      );
    });
  };
  await status([]);
  await user.selectOptions(
    screen.getByRole("option", { name: "PPTX" }).parentElement!,
    "pptx",
  );
  expect(screen.queryByRole("option", { name: "覆盖现有文件" })).toBeNull();
  await user.selectOptions(screen.getByLabelText("资料目录"), "ro");
  await user.selectOptions(screen.getByLabelText("成果目录"), "rw");
  await user.type(screen.getByLabelText("源文件名"), "项目进展.pptx");
  await user.type(screen.getByLabelText("新文件名"), "项目汇报.pptx");
  await user.type(screen.getByLabelText("任务描述"), "整理项目进展");
  await user.click(screen.getByRole("button", { name: "生成 PPTX" }));
  expect(
    JSON.parse(
      decodeURIComponent(
        invoke.mock.calls.at(-1)![0].split("desktop:docx:")[1],
      ),
    ),
  ).toEqual({
    action: "run",
    format: "pptx",
    source_grant: "ro",
    source: "项目进展.pptx",
    output_grant: "rw",
    output: "项目汇报.pptx",
    instruction: "整理项目进展",
  });
  await status([
    {
      id: "1",
      format: "pptx",
      status: "completed",
      output: "/成果/项目汇报.pptx",
    },
  ]);
  expect(screen.getByText("/成果/项目汇报.pptx")).toBeInTheDocument();
});

it("submits a text PDF with an eligible expert using the keyboard", async () => {
  const { localExpertsApi } = await import("../../api/modules/localExperts");
  const base = {
    description: "",
    system_prompt: "",
    default_model: "intranet/chat",
    skill_package_ids: [],
    knowledge_base_ids: [],
    published_expert_id: null,
  };
  vi.mocked(localExpertsApi.list).mockResolvedValueOnce([
    { ...base, agent_id: "pdf", name: "PDF 专家", actions: ["local_pdf"] },
    { ...base, agent_id: "docx", name: "DOCX 专家", actions: ["local_docx"] },
  ]);
  const invoke = vi.fn();
  (window as Window & { _wails?: unknown })._wails = { invoke };
  render(<LocalDocxPanel deviceId="mine" />);
  await act(async () =>
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: false,
          grants: [
            { id: "ro", path: "/资料", mode: "ro" },
            {
              id: "rw",
              path: "/成果",
              mode: "rw",
              overwrite: true,
              destructive: true,
            },
          ],
          tasks: [],
        },
      }),
    ),
  );
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("文件类型"), "pdf");
  expect(screen.getByText(/仅.*可选.*文字/)).toBeInTheDocument();
  expect(screen.queryByLabelText("文件操作")).toBeNull();
  await screen.findByRole("option", { name: "PDF 专家" });
  expect(screen.queryByRole("option", { name: "DOCX 专家" })).toBeNull();
  expect(
    screen.getByRole("option", { name: "不使用专家" }),
  ).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("任务专家"), "pdf");
  await user.selectOptions(screen.getByLabelText("资料目录"), "ro");
  expect(
    screen.getByLabelText("成果目录").querySelector('option[value="ro"]'),
  ).toBeNull();
  await user.selectOptions(screen.getByLabelText("成果目录"), "rw");
  await user.type(screen.getByLabelText("源文件名"), "source.pdf");
  await user.type(screen.getByLabelText("新文件名"), "result.pdf");
  await user.type(screen.getByLabelText("任务描述"), "整理内容");
  await act(async () => {
    screen.getByRole("button", { name: "生成 PDF" }).focus();
  });
  await user.keyboard("{Enter}");
  expect(
    JSON.parse(
      decodeURIComponent(
        invoke.mock.calls.at(-1)![0].split("desktop:docx:")[1],
      ),
    ),
  ).toEqual({
    action: "run",
    format: "pdf",
    source_grant: "ro",
    source: "source.pdf",
    output_grant: "rw",
    output: "result.pdf",
    instruction: "整理内容",
    agent_id: "pdf",
  });
  await act(async () =>
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: false,
          grants: [],
          tasks: [
            {
              id: "1",
              format: "pdf",
              status: "completed",
              output: "/成果/result.pdf",
            },
          ],
        },
      }),
    ),
  );
  expect(screen.getByLabelText("本机任务记录")).toHaveAttribute(
    "aria-live",
    "polite",
  );
  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(screen.getByText("/成果/result.pdf")).toBeInTheDocument();
});

it("distinguishes execution, awaiting confirmation, completed, failed, cancelled, and needs check statuses", async () => {
  (window as Window & { _wails?: unknown })._wails = { invoke: vi.fn() };
  render(<LocalDocxPanel deviceId="mine" />);
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: false,
          grants: [],
          tasks: [
            {
              id: "t1",
              status: "running",
              instruction: "任务1",
              output: "out1.docx",
            },
            {
              id: "t2",
              status: "awaiting_confirmation",
              instruction: "任务2",
              output: "out2.docx",
            },
            {
              id: "t3",
              status: "completed",
              instruction: "任务3",
              output: "out3.docx",
            },
            {
              id: "t4",
              status: "failed",
              error: "生成失败",
              instruction: "任务4",
              output: "out4.docx",
            },
            {
              id: "t5",
              status: "cancelled",
              instruction: "任务5",
              output: "out5.docx",
            },
            {
              id: "t6",
              status: "interrupted",
              instruction: "任务6",
              output: "out6.docx",
            },
          ],
        },
      }),
    );
  });

  expect(screen.getByText("处理中")).toBeInTheDocument();
  expect(screen.getByText("等待确认")).toBeInTheDocument();
  expect(screen.getByText("已完成")).toBeInTheDocument();
  expect(screen.getByText("失败")).toBeInTheDocument();
  expect(screen.getByText("已取消")).toBeInTheDocument();
  expect(screen.getByText("需要核对")).toBeInTheDocument();
});

it("allows cancelling running task and awaiting confirmation task", async () => {
  const invoke = vi.fn();
  (window as Window & { _wails?: unknown })._wails = { invoke };
  const user = userEvent.setup();
  render(<LocalDocxPanel deviceId="mine" />);
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: true,
          grants: [],
          tasks: [
            {
              id: "run-1",
              status: "running",
              instruction: "进行中任务",
              output: "out.docx",
            },
          ],
        },
      }),
    );
  });

  const cancelBtn = screen.getByRole("button", { name: "取消任务" });
  await user.click(cancelBtn);
  const command = JSON.parse(
    decodeURIComponent(invoke.mock.calls.at(-1)![0].split("desktop:docx:")[1]),
  );
  expect(command).toEqual({ action: "cancel", task_id: "run-1" });
});

it("displays failure reason, completed steps and retains artifact on failed task", async () => {
  (window as Window & { _wails?: unknown })._wails = { invoke: vi.fn() };
  render(<LocalDocxPanel deviceId="mine" />);
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: false,
          grants: [],
          tasks: [
            {
              id: "fail-1",
              status: "failed",
              error: "模型服务异常",
              instruction: "生成文档",
              output: "/成果/可用产物.docx",
              steps: ["已读取源文件", "已完成文本整理"],
            },
          ],
        },
      }),
    );
  });

  expect(screen.getByText("失败")).toBeInTheDocument();
  expect(screen.getByText("模型服务异常")).toBeInTheDocument();
  expect(screen.getByText("已读取源文件")).toBeInTheDocument();
  expect(screen.getByText("已完成文本整理")).toBeInTheDocument();
  expect(screen.getByText("/成果/可用产物.docx")).toBeInTheDocument();
});
