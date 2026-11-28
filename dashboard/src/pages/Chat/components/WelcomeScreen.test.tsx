import { readFileSync } from "node:fs";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import AgentNotReadyScreen from "./AgentNotReadyScreen";
import ModelConfigEmpty from "./ModelConfigEmpty";
import WelcomeScreen from "./WelcomeScreen";

describe("Colink welcome", () => {
  it.each([
    ["without an expert", <AgentNotReadyScreen agent={null} noAgents />],
    ["without a model", <ModelConfigEmpty canConfigure />],
  ])("uses the ring %s and still opens setup immediately", (_, content) => {
    render(
      <MemoryRouter>
        <Routes>
          <Route path="/" element={content} />
          <Route path="/experts" element={<h1>专家配置</h1>} />
          <Route path="/admin/models" element={<h1>模型配置</h1>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("img", { name: "Colink" })).toBeVisible();
    expect(document.querySelector('img[src*="octop-mascot"]')).toBeNull();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("heading", { name: /配置/ })).toBeVisible();
  });

  it("shows the ring without granting model configuration permission", () => {
    render(
      <MemoryRouter>
        <ModelConfigEmpty canConfigure={false} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("img", { name: "Colink" })).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("uses the approved ring geometry without the old mascot or a square background", () => {
    render(
      <WelcomeScreen
        conversationKey="agent-a:"
        quickCards={[]}
        onPromptClick={vi.fn()}
      />,
    );

    const mark = screen.getByRole("img", { name: "Colink" });
    const approved = new DOMParser().parseFromString(
      readFileSync("public/colink-mark-black.svg", "utf8"),
      "image/svg+xml",
    );
    expect(
      [...mark.querySelectorAll("path")].map((p) => p.getAttribute("d")),
    ).toEqual(
      [...approved.querySelectorAll("path")].map((p) => p.getAttribute("d")),
    );
    expect(mark.querySelector("rect, image")).toBeNull();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(document.querySelector('img[src*="octop-mascot"]')).toBeNull();
  });

  it("keeps the same mark during rerenders and streaming visibility changes", () => {
    const props = {
      conversationKey: "agent-a:",
      quickCards: [],
      onPromptClick: vi.fn(),
    };
    const { rerender } = render(<WelcomeScreen {...props} />);
    const mark = screen.getByRole("img", { name: "Colink" });
    rerender(<WelcomeScreen {...props} agentName="示例专家" hideMark />);
    expect(mark).toBeInTheDocument();
    expect(mark).not.toBeVisible();
    rerender(<WelcomeScreen {...props} agentName="示例专家" />);
    expect(screen.getByRole("img", { name: "Colink" })).toBe(mark);
    expect(mark).toBeVisible();
  });

  it("accepts a quick prompt immediately and allows leaving during the intro", () => {
    const onPromptClick = vi.fn();
    const { unmount } = render(
      <WelcomeScreen
        conversationKey="agent-a:"
        quickCards={[
          {
            title: "整理资料",
            description: "示例",
            prompt: "整理示例资料",
            color: "#345F90",
          },
        ]}
        onPromptClick={onPromptClick}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "整理资料 示例" }));
    expect(onPromptClick).toHaveBeenCalledWith("整理示例资料", {
      prefill: false,
    });
    unmount();
    expect(
      screen.queryByRole("img", { name: "Colink" }),
    ).not.toBeInTheDocument();
  });
});
