import { act, renderHook, waitFor } from "@testing-library/react";
import type { ClipboardEvent, DragEvent } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { message } from "@/utils/antdMessage";
import { uploadFile } from "../../../api/modules/upload";
import { DESKTOP_FILE_DROP_EVENT } from "../../../utils/desktopFileDrop";
import { useChatAttachments } from "./useChatAttachments";

vi.mock("../../../hooks/useServerUploadLimit", () => ({
  useServerUploadLimit: () => ({ maxUploadBytes: 1024, maxUploadMb: 1 }),
}));

vi.mock("../../../api/modules/upload", () => ({
  uploadFile: vi.fn(),
}));

vi.mock("@/utils/antdMessage", () => ({
  message: { error: vi.fn(), warning: vi.fn() },
}));

function dragEvent(
  init: Partial<DragEvent<HTMLElement>> & { currentTarget?: HTMLElement },
): DragEvent<HTMLElement> {
  return {
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
    dataTransfer: { types: ["Files"], items: [], files: [] },
    relatedTarget: null,
    ...init,
  } as unknown as DragEvent<HTMLElement>;
}

describe("useChatAttachments drop", () => {
  const confirmUpload = vi.fn(async () => true);
  beforeEach(() => {
    confirmUpload.mockReset().mockResolvedValue(true);
    vi.mocked(uploadFile).mockReset();
    vi.mocked(message.error).mockReset();
    vi.mocked(message.warning).mockReset();
    vi.mocked(uploadFile).mockImplementation(async (_agentId, file) => ({
      path: `inbound/${file.name}`,
      workspace_path: `inbound/${file.name}`,
      filename: file.name,
      media_type: file.type || "application/octet-stream",
      url: "",
      access_url: `/files/${file.name}`,
    }));
  });

  it("does not upload pasted originals when consent is refused", async () => {
    confirmUpload.mockResolvedValue(false);
    const { result } = renderHook(() =>
      useChatAttachments("agent-1", { confirmUpload }),
    );
    const file = new File(["private"], "private.txt", { type: "text/plain" });
    await act(async () => {
      result.current.handlePaste({
        preventDefault: vi.fn(),
        clipboardData: { items: [{ kind: "file", getAsFile: () => file }] },
      } as unknown as ClipboardEvent);
    });
    expect(confirmUpload).toHaveBeenCalledWith([file]);
    expect(uploadFile).not.toHaveBeenCalled();
    expect(result.current.attachments).toEqual([]);
  });

  it("waits for consent and discards it when the conversation changes", async () => {
    let approve!: (allowed: boolean) => void;
    confirmUpload.mockImplementation(
      () =>
        new Promise((resolve) => {
          approve = resolve;
        }),
    );
    const { result, rerender } = renderHook(
      ({ threadId }) =>
        useChatAttachments("agent-1", { threadId, confirmUpload }),
      { initialProps: { threadId: "first" } },
    );
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.processFiles([
        new File(["private"], "private.txt"),
      ]);
    });
    expect(result.current.uploading).toBe(true);
    expect(uploadFile).not.toHaveBeenCalled();
    rerender({ threadId: "second" });
    await act(async () => {
      approve(true);
      await pending;
    });
    expect(uploadFile).not.toHaveBeenCalled();
    expect(result.current.attachments).toEqual([]);
    expect(result.current.uploading).toBe(false);
  });

  it("does not attach an upload response to another conversation", async () => {
    let complete!: (value: Awaited<ReturnType<typeof uploadFile>>) => void;
    vi.mocked(uploadFile).mockImplementation(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    const { result, rerender } = renderHook(
      ({ threadId }) =>
        useChatAttachments("agent-1", { threadId, confirmUpload }),
      { initialProps: { threadId: "first" } },
    );
    let pending!: Promise<void>;
    await act(async () => {
      pending = result.current.processFiles([
        new File(["private"], "private.txt"),
      ]);
    });
    rerender({ threadId: "second" });
    await act(async () => {
      complete({
        path: "inbound/private.txt",
        workspace_path: "inbound/private.txt",
        filename: "private.txt",
        media_type: "text/plain",
        url: "",
        access_url: "/private.txt",
      });
      await pending;
    });
    expect(result.current.attachments).toEqual([]);
  });

  it("uploads every file from one drop", async () => {
    const { result } = renderHook(() =>
      useChatAttachments("agent-1", { confirmUpload }),
    );
    const first = new File(["a"], "a.txt", { type: "text/plain" });
    const second = new File(["b"], "b.txt", { type: "text/plain" });
    const dropped = dragEvent({
      dataTransfer: {
        types: ["Files"],
        items: [
          { kind: "file", getAsFile: () => first },
          { kind: "file", getAsFile: () => second },
        ],
        files: [first, second],
      } as unknown as DataTransfer,
    });

    await act(async () => {
      await result.current.handleDrop(dropped);
    });

    await waitFor(() => {
      expect(result.current.attachments.map((item) => item.filename)).toEqual([
        "a.txt",
        "b.txt",
      ]);
    });
    expect(uploadFile).toHaveBeenCalledTimes(2);
    expect(result.current.dragOver).toBe(false);
  });

  it("keeps the drop highlight while the pointer is still inside the composer", () => {
    const { result } = renderHook(() =>
      useChatAttachments("agent-1", { confirmUpload }),
    );
    const shell = document.createElement("div");
    const field = document.createElement("textarea");
    shell.appendChild(field);

    act(() => {
      result.current.handleDragEnter(
        dragEvent({
          currentTarget: shell,
          dataTransfer: { types: ["Files"] } as DataTransfer,
        }),
      );
    });
    expect(result.current.dragOver).toBe(true);

    act(() => {
      result.current.handleDragLeave(
        dragEvent({ currentTarget: shell, relatedTarget: field }),
      );
    });
    expect(result.current.dragOver).toBe(true);

    act(() => {
      result.current.handleDragLeave(
        dragEvent({ currentTarget: shell, relatedTarget: document.body }),
      );
    });
    expect(result.current.dragOver).toBe(false);
  });

  it("does not highlight a text drag", () => {
    const { result } = renderHook(() =>
      useChatAttachments("agent-1", { confirmUpload }),
    );
    const event = dragEvent({
      dataTransfer: {
        types: ["text/plain"],
        items: [],
        files: [],
      } as unknown as DataTransfer,
    });

    act(() => {
      result.current.handleDragEnter(event);
    });

    expect(result.current.dragOver).toBe(false);
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  it("warns when a dropped folder has no files", async () => {
    const { result } = renderHook(() =>
      useChatAttachments("agent-1", { confirmUpload }),
    );
    const emptyDir = {
      isFile: false,
      isDirectory: true,
      name: "empty",
      fullPath: "/empty",
      createReader: () => ({
        readEntries(success: (entries: FileSystemEntry[]) => void) {
          success([]);
        },
      }),
    };
    const dropped = dragEvent({
      dataTransfer: {
        types: ["Files"],
        items: [
          {
            kind: "file",
            webkitGetAsEntry: () => emptyDir,
            getAsFile: () => null,
          },
        ],
        files: [],
      } as unknown as DataTransfer,
    });

    await act(async () => {
      await result.current.handleDrop(dropped);
    });

    expect(uploadFile).not.toHaveBeenCalled();
    expect(message.warning).toHaveBeenCalledWith("No files to upload");
  });

  it("uploads files delivered by the desktop shell", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("note", {
            status: 200,
            headers: { "Content-Type": "text/plain" },
          }),
      ),
    );
    const { result } = renderHook(() =>
      useChatAttachments("agent-1", { confirmUpload }),
    );

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(DESKTOP_FILE_DROP_EVENT, {
          detail: {
            files: [
              {
                name: "note.txt",
                url: "http://127.0.0.1:9/drop/token/0",
                type: "text/plain",
              },
            ],
            truncated: true,
            max: 500,
          },
        }),
      );
    });

    await waitFor(() => {
      expect(result.current.attachments.map((item) => item.filename)).toEqual([
        "note.txt",
      ]);
    });
    expect(uploadFile).toHaveBeenCalledTimes(1);
    expect(message.warning).toHaveBeenCalledWith(
      "Only the first 500 files can be uploaded at once",
    );
    expect(result.current.dragOver).toBe(false);
    vi.unstubAllGlobals();
  });

  it("discards a delayed desktop drop when the composer is cleared", async () => {
    let loaded!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise((resolve) => {
            loaded = resolve;
          }),
      ),
    );
    const { result } = renderHook(() =>
      useChatAttachments("agent-1", { confirmUpload }),
    );
    act(() => {
      window.dispatchEvent(
        new CustomEvent(DESKTOP_FILE_DROP_EVENT, {
          detail: {
            files: [
              {
                name: "note.txt",
                url: "http://127.0.0.1:9/drop/token/0",
                type: "text/plain",
              },
            ],
          },
        }),
      );
      result.current.clearAttachments();
    });
    await act(async () => {
      loaded(
        new Response("note", {
          status: 200,
          headers: { "Content-Type": "text/plain" },
        }),
      );
    });
    expect(confirmUpload).not.toHaveBeenCalled();
    expect(uploadFile).not.toHaveBeenCalled();
    expect(result.current.attachments).toEqual([]);
    vi.unstubAllGlobals();
  });
});
