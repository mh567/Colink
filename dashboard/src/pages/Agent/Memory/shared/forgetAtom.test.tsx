import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  memoryDashboardApi,
  type AtomItem,
} from "../../../../api/modules/memoryDashboard";
import { message } from "@/utils/antdMessage";
import { modal } from "@/utils/antdModal";
import { confirmForgetAtom } from "./forgetAtom";

vi.mock("@/i18n", () => ({
  default: {
    t: (key: string, fallback?: string | Record<string, unknown>) => {
      if (typeof fallback === "string") return fallback;
      if (
        fallback &&
        typeof fallback === "object" &&
        "defaultValue" in fallback
      ) {
        return (fallback as { defaultValue?: string }).defaultValue ?? key;
      }
      return key;
    },
  },
}));

vi.mock("@/utils/antdModal", () => ({
  modal: { confirm: vi.fn() },
}));

vi.mock("@/utils/antdMessage", () => ({
  message: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock("../../../../api/modules/memoryDashboard", () => ({
  memoryDashboardApi: {
    forgetAtom: vi.fn(),
  },
}));

const mockModalConfirm = vi.mocked(modal.confirm);
const mockMessageSuccess = vi.mocked(message.success);
const mockMessageError = vi.mocked(message.error);
const mockForgetAtom = vi.mocked(memoryDashboardApi.forgetAtom);

describe("confirmForgetAtom", () => {
  const dummyAtom: AtomItem = {
    id: "atom-123",
    assertion: "员工偏好：午后进行代码审查",
    entity_id: "ent-1",
    entity_name: "Alice",
    entity_type: "User",
    kind: "Preference",
    created_at: "2026-11-20T00:00:00Z",
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("opens modal with assertion and warning distinguishing memory from session and audit", () => {
    mockModalConfirm.mockImplementation((config) => {
      render(<>{config.content as ReactNode}</>);
      return { destroy: vi.fn(), update: vi.fn() } as unknown as ReturnType<
        typeof modal.confirm
      >;
    });

    confirmForgetAtom({
      agentId: "agent-a",
      atom: dummyAtom,
    });

    expect(mockModalConfirm).toHaveBeenCalledOnce();
    expect(screen.getByText("员工偏好：午后进行代码审查")).toBeInTheDocument();
    expect(
      screen.getByText(/此操作不删除法定90天安全审计日志，亦不影响会话记录/),
    ).toBeInTheDocument();
  });

  it("calls memoryDashboardApi.forgetAtom on confirm and triggers onSuccess", async () => {
    let capturedOnOk: (() => Promise<void>) | undefined;
    mockModalConfirm.mockImplementation((config) => {
      capturedOnOk = config.onOk as () => Promise<void>;
      return { destroy: vi.fn(), update: vi.fn() } as unknown as ReturnType<
        typeof modal.confirm
      >;
    });

    const onSuccess = vi.fn();
    confirmForgetAtom({
      agentId: "agent-a",
      atom: dummyAtom,
      onSuccess,
    });

    mockForgetAtom.mockResolvedValueOnce({
      atom_id: dummyAtom.id,
      forgotten: true,
    });

    expect(capturedOnOk).toBeDefined();
    await capturedOnOk!();

    expect(mockForgetAtom).toHaveBeenCalledWith("agent-a", "atom-123");
    expect(mockMessageSuccess).toHaveBeenCalled();
    expect(onSuccess).toHaveBeenCalledOnce();
  });

  it("handles forget error and displays error message", async () => {
    let capturedOnOk: (() => Promise<void>) | undefined;
    mockModalConfirm.mockImplementation((config) => {
      capturedOnOk = config.onOk as () => Promise<void>;
      return { destroy: vi.fn(), update: vi.fn() } as unknown as ReturnType<
        typeof modal.confirm
      >;
    });

    confirmForgetAtom({
      agentId: "agent-a",
      atom: dummyAtom,
    });

    mockForgetAtom.mockRejectedValueOnce(new Error("Network failure"));

    await expect(capturedOnOk!()).rejects.toThrow("Network failure");
    expect(mockMessageError).toHaveBeenCalledWith(
      expect.stringContaining("Network failure"),
    );
  });
});
