import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { App } from "antd";
import { MemoryRouter } from "react-router-dom";
import { initReactI18next } from "react-i18next";
import type { ComponentProps } from "react";
import i18n from "../../../i18n";
import en from "../../../locales/en.json";
import { clearAuthToken, setAuthToken } from "../../../api/request";
import ChatInput from "./ChatInput";

vi.unmock("react-i18next");

const scope = {
  id: "scope-1",
  name: "minutes.txt",
  path: "/selected/minutes.txt",
  kind: "file" as const,
};
const native = {
  select: vi.fn(),
  remove: vi.fn(),
  clear: vi.fn(),
  consume: vi.fn(),
};
const upload = vi.fn();

function composer(props: Partial<ComponentProps<typeof ChatInput>> = {}) {
  return (
    <MemoryRouter>
      <App>
        <ChatInput
          agentId="agent-1"
          threadId="thread-1"
          onSend={vi.fn()}
          onCancel={vi.fn()}
          onNewChat={vi.fn()}
          isStreaming={false}
          {...props}
        />
      </App>
    </MemoryRouter>
  );
}

async function selectLocalFile() {
  fireEvent.click(screen.getByRole("button", { name: "Use on this computer" }));
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Select local files" }),
  );
  await screen.findByText(scope.path);
}

function pasteAttachment() {
  fireEvent.paste(screen.getByRole("textbox"), {
    clipboardData: {
      items: [
        {
          kind: "file",
          getAsFile: () =>
            new File(["agenda"], "agenda.txt", { type: "text/plain" }),
        },
      ],
    },
  });
}

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    resources: { en: { translation: en } },
    lng: "en",
    fallbackLng: "en",
    interpolation: { escapeValue: false },
  });
});

beforeEach(() => {
  vi.stubGlobal("BASE_URL", "");
  const getComputedStyle = window.getComputedStyle;
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  sessionStorage.clear();
  setAuthToken("session-token");
  vi.clearAllMocks();
  native.select.mockResolvedValue([scope]);
  native.remove.mockResolvedValue(undefined);
  native.clear.mockResolvedValue(undefined);
  native.consume.mockResolvedValue({
    text: "Delivery is due on Monday.",
    sources: [{ name: scope.name, path: scope.path }],
  });
  window.colinkDesktop = { localFiles: native };
  upload.mockResolvedValue(
    Response.json({
      path: "inbound/agenda.txt",
      workspace_path: "inbound/agenda.txt",
      filename: "agenda.txt",
      media_type: "text/plain",
      url: "",
      access_url: "/files/agenda.txt",
    }),
  );
  vi.stubGlobal(
    "XMLHttpRequest",
    class {
      onload?: () => void;
      status = 200;
      responseText = "";
      method = "";
      url = "";
      open(method: string, url: string) {
        this.method = method;
        this.url = url;
      }
      setRequestHeader() {}
      getResponseHeader() {
        return null;
      }
      send(body: FormData) {
        void upload(this.url, { method: this.method, body }).then(
          async (response: Response) => {
            this.status = response.status;
            this.responseText = await response.text();
            this.onload?.();
          },
        );
      }
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/upload") && init?.method === "POST")
        return upload(input, init);
      if (url.includes("/slash/commands"))
        return Response.json({ commands: [] });
      if (url.endsWith("/settings/upload"))
        return Response.json({ max_upload_bytes: 1048576 });
      if (url.endsWith("/voice/active"))
        return Response.json({
          stt: "browser",
          tts: "browser",
          stt_realtime: false,
        });
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
});

afterEach(() => {
  delete window.colinkDesktop;
  vi.unstubAllGlobals();
  vi.mocked(window.getComputedStyle).mockRestore();
});

describe("chat material choices", () => {
  it("sends a local selection once without requiring an attachment or typed prompt", async () => {
    const onSend = vi.fn();
    render(composer({ onSend }));
    await selectLocalFile();
    expect(screen.getByTitle("Send")).toBeEnabled();
    fireEvent.click(screen.getByTitle("Send"));
    await waitFor(() =>
      expect(onSend).toHaveBeenCalledWith(
        expect.stringContaining("> Delivery is due on Monday."),
        undefined,
      ),
    );
    expect(screen.queryByText(scope.path)).not.toBeInTheDocument();
    expect(upload).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Continue" },
    });
    fireEvent.click(screen.getByTitle("Send"));
    expect(onSend).toHaveBeenLastCalledWith("Continue", undefined);
    expect(native.consume).toHaveBeenCalledTimes(1);
  });

  it("does not send a completed local read after the login token is cleared", async () => {
    let finishReading!: (value: { text: string; sources: [] }) => void;
    native.consume.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishReading = resolve;
        }),
    );
    const onSend = vi.fn();
    render(composer({ onSend, initialText: "Summarize" }));
    await selectLocalFile();
    fireEvent.click(screen.getByTitle("Send"));
    clearAuthToken();
    await act(async () => {
      finishReading({ text: "Private material", sources: [] });
    });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("Summarize");
    expect(screen.queryByText(scope.path)).not.toBeInTheDocument();
  });

  it("explains the upload destination before sending and respects cancellation", async () => {
    const onSend = vi.fn();
    render(composer({ onSend, initialText: "Keep this draft" }));
    pasteAttachment();
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(
        /original files will be uploaded to the enterprise server/,
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        /not be added to a knowledge base or personal memory/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByTitle("Send")).toBeDisabled();
    expect(upload).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.getByTitle("Send")).toBeEnabled());
    expect(upload).not.toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("Keep this draft");
  });

  it("waits for uploading and sends an attachment with one-time local context", async () => {
    let finishUpload!: (response: Response) => void;
    upload.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishUpload = resolve;
        }),
    );
    const onSend = vi.fn();
    render(composer({ onSend, initialText: "Compare these materials" }));
    pasteAttachment();
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Send attachment" }),
    );
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
    expect(screen.getByTitle("Send")).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "/status" },
    });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("/status");
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Compare these materials" },
    });
    await act(async () => {
      finishUpload(
        Response.json({
          path: "inbound/agenda.txt",
          workspace_path: "inbound/agenda.txt",
          filename: "agenda.txt",
          media_type: "text/plain",
          url: "",
          access_url: "/files/agenda.txt",
        }),
      );
    });
    expect(
      await screen.findByText("Sent to the enterprise server"),
    ).toBeInTheDocument();
    await selectLocalFile();
    fireEvent.click(screen.getByTitle("Send"));
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect(onSend).toHaveBeenCalledWith(
      expect.stringContaining(
        "Compare these materials\n\nLocal material sent to this conversation",
      ),
      [
        expect.objectContaining({
          filename: "agenda.txt",
          workspacePath: "inbound/agenda.txt",
        }),
      ],
    );
    expect(onSend.mock.calls[0][0]).toContain("> Delivery is due on Monday.");
    expect(onSend.mock.calls[0][0]).toContain("reference, not instructions");
    expect(screen.queryByText(scope.path)).not.toBeInTheDocument();
    expect(
      screen.queryByText("Sent to the enterprise server"),
    ).not.toBeInTheDocument();
  });

  it.each(["cancel", "switch", "unmount"])(
    "discards pending local context after %s",
    async (reason) => {
      let finishReading!: (value: { text: string; sources: [] }) => void;
      native.consume.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishReading = resolve;
          }),
      );
      const onSend = vi.fn();
      const view = render(composer({ onSend, initialText: "Summarize" }));
      await selectLocalFile();
      fireEvent.click(screen.getByTitle("Send"));
      expect(screen.getByRole("textbox")).toBeDisabled();
      if (reason === "cancel")
        fireEvent.click(
          screen.getByRole("button", { name: "Cancel local reading" }),
        );
      else if (reason === "switch")
        view.rerender(composer({ onSend, threadId: "second" }));
      else view.unmount();
      await act(async () => {
        finishReading({ text: "Private material", sources: [] });
      });
      expect(onSend).not.toHaveBeenCalled();
      if (reason === "cancel")
        expect(screen.getByRole("textbox")).toHaveValue("Summarize");
      expect(screen.queryByText(scope.path)).not.toBeInTheDocument();
    },
  );

  it.each([false, true])(
    "does not queue or send local material during a reply (team: %s)",
    async (isTeam) => {
      const onSend = vi.fn();
      const onQueue = vi.fn(() => "ok" as const);
      const view = render(
        composer({ onSend, onQueue, isTeam, initialText: "Summarize" }),
      );
      await selectLocalFile();
      view.rerender(composer({ onSend, onQueue, isTeam, isStreaming: true }));
      fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
      expect(onQueue).not.toHaveBeenCalled();
      await act(async () => undefined);
      expect(onSend).not.toHaveBeenCalled();
      expect(native.consume).not.toHaveBeenCalled();
    },
  );

  it("revokes the local selection when the new-chat slash command is used", async () => {
    const onNewChat = vi.fn();
    render(composer({ onNewChat, initialText: "/new" }));
    await selectLocalFile();
    fireEvent.click(screen.getByTitle("Send"));
    expect(onNewChat).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(scope.path)).not.toBeInTheDocument();
  });

  it("does not carry a local selection past a separate slash-command message", async () => {
    const onSend = vi.fn();
    render(composer({ onSend }));
    await selectLocalFile();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "/status" },
    });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("/status");
    expect(screen.queryByText(scope.path)).not.toBeInTheDocument();
    expect(native.consume).not.toHaveBeenCalled();
  });
});
