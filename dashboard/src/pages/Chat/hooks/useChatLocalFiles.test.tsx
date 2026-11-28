import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setAuthToken, UNAUTHORIZED_EVENT } from "../../../api/request";
import { useChatLocalFiles } from "./useChatLocalFiles";

describe("local material in a conversation", () => {
  const native = {
    select: vi.fn(),
    remove: vi.fn(async () => undefined),
    clear: vi.fn(async () => undefined),
    consume: vi.fn(),
  };
  const scope = {
    id: "scope-1",
    name: "纪要.txt",
    path: "/selected/纪要.txt",
    kind: "file" as const,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    setAuthToken("session-token");
    window.colinkDesktop = { localFiles: native };
    native.select.mockResolvedValue([scope]);
    native.consume.mockResolvedValue({
      text: "会议决定下周一交付。",
      sources: [{ name: scope.name, path: scope.path }],
    });
  });

  afterEach(() => {
    delete window.colinkDesktop;
    vi.unstubAllGlobals();
  });

  it("supports an HTTP client without crypto.randomUUID", async () => {
    vi.stubGlobal("crypto", {
      getRandomValues: crypto.getRandomValues.bind(crypto),
    });
    const { result } = renderHook(() => useChatLocalFiles("agent-1", null));
    await act(async () => {
      await result.current.select("files");
    });
    expect(result.current.scopes).toEqual([scope]);
    expect(native.select).toHaveBeenCalledWith(
      expect.objectContaining({
        conversationId: expect.stringMatching(/^draft:[a-f0-9]+$/),
      }),
    );
  });

  it("uses native selection once and returns quoted context without an attachment", async () => {
    const { result } = renderHook(() =>
      useChatLocalFiles("agent-1", "thread-1"),
    );
    await act(async () => {
      await result.current.select("files");
    });
    expect(result.current.scopes).toEqual([scope]);
    let content: string | null = null;
    await act(async () => {
      content = await result.current.consume("总结纪要");
    });
    expect(content).toContain("总结纪要");
    expect(content).toContain("会议决定下周一交付。");
    expect(content).toContain("纪要.txt");
    expect(result.current.scopes).toEqual([]);
    await act(async () => {
      expect(await result.current.consume("继续聊天")).toBe("继续聊天");
    });
    expect(native.consume).toHaveBeenCalledTimes(1);
  });

  it("removes a selected file before sending and never reads it", async () => {
    const { result } = renderHook(() =>
      useChatLocalFiles("agent-1", "thread-1"),
    );
    await act(async () => {
      await result.current.select("files");
    });
    await act(async () => {
      await result.current.remove(scope.id);
    });
    expect(result.current.scopes).toEqual([]);
    await act(async () => {
      expect(await result.current.consume("普通聊天")).toBe("普通聊天");
    });
    expect(native.consume).not.toHaveBeenCalled();
  });

  it("drops a native file result when the user moves to another conversation", async () => {
    let selected!: (value: (typeof scope)[]) => void;
    native.select.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          selected = resolve;
        }),
    );
    const { result, rerender } = renderHook(
      ({ threadId }) => useChatLocalFiles("agent-1", threadId),
      { initialProps: { threadId: "first" } },
    );
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.select("files");
    });
    rerender({ threadId: "second" });
    await act(async () => {
      selected([scope]);
      await pending;
    });
    expect(result.current.scopes).toEqual([]);
    expect(result.current.busy).toBe(false);
  });

  it("keeps the new conversation selection when a previous removal fails", async () => {
    let rejectRemoval!: (cause: Error) => void;
    native.remove.mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          rejectRemoval = reject;
        }),
    );
    const { result, rerender } = renderHook(
      ({ threadId }) => useChatLocalFiles("agent-1", threadId),
      { initialProps: { threadId: "first" } },
    );
    await act(async () => {
      await result.current.select("files");
    });
    let removing!: Promise<void>;
    act(() => {
      removing = result.current.remove(scope.id);
    });
    rerender({ threadId: "second" });
    await act(async () => {
      await result.current.select("files");
    });
    await act(async () => {
      rejectRemoval(new Error("LOCAL_SCOPE_REVOKED"));
      await removing;
    });
    expect(result.current.scopes).toEqual([scope]);
    expect(result.current.error).toBeNull();
  });

  it.each(["cancel", "unauthorized", "offline"])(
    "discards a read result after %s",
    async (reason) => {
      let read!: (value: {
        text: string;
        sources: { name: string; path: string }[];
      }) => void;
      native.consume.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            read = resolve;
          }),
      );
      const { result } = renderHook(() =>
        useChatLocalFiles("agent-1", "first"),
      );
      await act(async () => {
        await result.current.select("files");
      });
      let pending!: Promise<string | null>;
      act(() => {
        pending = result.current.consume("总结");
      });
      act(() => {
        if (reason === "cancel") result.current.clear();
        else
          window.dispatchEvent(
            new Event(
              reason === "unauthorized" ? UNAUTHORIZED_EVENT : "offline",
            ),
          );
      });
      await act(async () => {
        read({ text: "private", sources: [scope] });
        expect(await pending).toBeNull();
      });
      expect(result.current.scopes).toEqual([]);
    },
  );

  it("reports a native refusal without sending or retaining the selected range", async () => {
    native.consume.mockRejectedValueOnce(
      new Error("Error invoking remote method: Error: LOCAL_SCOPE_UNSAFE_PATH"),
    );
    const { result } = renderHook(() => useChatLocalFiles("agent-1", "first"));
    await act(async () => {
      await result.current.select("files");
    });
    await act(async () => {
      expect(await result.current.consume("总结")).toBeNull();
    });
    expect(result.current.error).toBe(
      "localFiles.errors.LOCAL_SCOPE_UNSAFE_PATH",
    );
    expect(result.current.scopes).toEqual([]);
  });

  it("revokes earlier native selections when another selection fails", async () => {
    const { result } = renderHook(() => useChatLocalFiles("agent-1", "first"));
    await act(async () => {
      await result.current.select("files");
    });
    native.clear.mockClear();
    native.select.mockRejectedValueOnce(
      new Error("LOCAL_SCOPE_UNSUPPORTED_FILE"),
    );
    await act(async () => {
      await result.current.select("files");
    });
    expect(result.current.scopes).toEqual([]);
    expect(result.current.error).toBe(
      "localFiles.errors.LOCAL_SCOPE_UNSUPPORTED_FILE",
    );
    expect(native.clear).toHaveBeenCalledTimes(1);
  });
});
