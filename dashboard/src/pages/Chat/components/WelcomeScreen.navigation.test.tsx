import { useState } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ResolvedModel } from "../../../api/types";
import { providerApi } from "../../../api/modules/provider";
import { useChatComposerResources } from "../hooks/useChatComposerResources";
import { useChatSessionActions } from "../hooks/useChatSessionActions";
import { EMPTY_CHAT_SESSION_KEY } from "../constants";
import * as chatStore from "../hooks/chatStore";
import ModelConfigEmpty from "./ModelConfigEmpty";
import WelcomeScreen from "./WelcomeScreen";

vi.mock("../../../api/modules/provider", () => ({
  providerApi: {
    listResolvedModels: vi.fn(),
    getActiveModel: vi.fn().mockResolvedValue(null),
  },
}));
vi.mock("../../../api/modules/connectors", () => ({
  connectorsApi: { listInstances: vi.fn().mockResolvedValue([]) },
}));
vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: {
    getCapability: vi.fn().mockResolvedValue({ usable: true }),
    list: vi.fn().mockResolvedValue([]),
  },
}));
vi.mock("../../../api/modules/preferences", () => ({
  preferencesApi: {
    get: vi.fn().mockResolvedValue(null),
    set: vi.fn().mockResolvedValue(undefined),
  },
}));
vi.mock("../../../api/request", () => ({
  request: vi.fn().mockResolvedValue(null),
}));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 7 }),
}));
vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({
    loading: false,
    agents: [
      { agent_id: "agent-a", name: "示例专家", state: "running" },
      { agent_id: "agent-b", name: "示例专家", state: "running" },
    ],
  }),
}));

const model: ResolvedModel = {
  provider_id: 1,
  provider_name: "example",
  provider_kind: "openai",
  model: "chat",
  name: "示例模型",
};
const noop = () => undefined;

function NewChatWelcome() {
  const { agentId } = useParams();
  const [input, setInput] = useState("");
  const { modelsReady, availableModels, setSelectedModel } =
    useChatComposerResources(agentId, null);
  const { handleNewChatWithAgent } = useChatSessionActions({
    resolvedAgentId: agentId,
    activeThreadId: null,
    sessions: [],
    isMobile: false,
    setActiveAgent: noop,
    setSidebarOpen: noop,
    setSelectedModel,
    setHasBrowserTool: noop,
    deleteSession: async () => true,
    clearMessages: noop,
    resetNavForAgentSwitch: noop,
    markInitialNavDone: noop,
  });
  return (
    <>
      <button onClick={() => handleNewChatWithAgent("agent-b")}>
        为 B 新建对话
      </button>
      <output>{`${agentId}:${modelsReady ? "ready" : "loading"}`}</output>
      <input
        aria-label="消息"
        value={input}
        onChange={(event) => setInput(event.target.value)}
      />
      {modelsReady && availableModels.length === 0 ? (
        <ModelConfigEmpty canConfigure />
      ) : (
        <WelcomeScreen
          conversationKey={`${agentId}:`}
          agentName="示例专家"
          quickCards={[]}
          onPromptClick={noop}
        />
      )}
    </>
  );
}

describe("new-chat welcome navigation", () => {
  afterEach(() => {
    chatStore.removeSession(EMPTY_CHAT_SESSION_KEY);
    document.documentElement.removeAttribute("data-theme");
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("starts a fresh mark for B without restarting after its model catalog or input changes", async () => {
    let resolveB!: (models: ResolvedModel[]) => void;
    const pendingB = new Promise<ResolvedModel[]>((resolve) => {
      resolveB = resolve;
    });
    vi.mocked(providerApi.listResolvedModels).mockImplementation((agentId) =>
      agentId === "agent-b" ? pendingB : Promise.resolve([model]),
    );
    render(
      <MemoryRouter initialEntries={["/chat/agent-a"]}>
        <Routes>
          <Route path="/chat/:agentId" element={<NewChatWelcome />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByText("agent-a:ready");
    const markA = screen.getByRole("img", { name: "Colink" });

    fireEvent.click(screen.getByRole("button", { name: "为 B 新建对话" }));
    await screen.findByText("agent-b:loading");
    const markB = screen.getByRole("img", { name: "Colink" });
    expect(markB.isSameNode(markA)).toBe(false);
    expect(markA.isConnected).toBe(false);

    await act(async () => resolveB([model]));
    await screen.findByText("agent-b:ready");
    fireEvent.change(screen.getByRole("textbox", { name: "消息" }), {
      target: { value: "示例输入" },
    });
    document.documentElement.setAttribute("data-theme", "dark");
    fireEvent.focus(window);
    await waitFor(() =>
      expect(screen.getByRole("img", { name: "Colink" })).toBe(markB),
    );
  });
});
