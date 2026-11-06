import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import LocalDocxPanel from "./LocalDocxPanel";

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
    await user.selectOptions(screen.getByLabelText("文件类型"), "ocr");
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
