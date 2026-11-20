import { App, ConfigProvider } from "antd";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  knowledgeBasesApi,
  type KnowledgeBase,
  type KnowledgeCapability,
  type KnowledgeDocument,
} from "../../api/modules/knowledgeBases";
import KnowledgeBasesPage from ".";

vi.mock("../../components/PdfDocumentPreview", () => ({ default: () => null }));
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

const personal: KnowledgeBase = {
  id: "private-kb",
  owner_user_id: 1,
  name: "Private library",
  description: "Personal files",
  default_open: false,
  shared: false,
  icon_name: "",
  embedding_model: "",
  embedding_dim: 0,
  doc_count: 1,
  max_documents: 100,
  created_at: 1,
  updated_at: 1,
};
const guide: KnowledgeDocument = {
  id: "guide-doc",
  kb_id: personal.id,
  filename: "guide.txt",
  content_type: "text/plain",
  byte_size: 14,
  content_hash: "test",
  status: "ready",
  error_message: "",
  chunk_count: 1,
  created_at: 1,
  updated_at: 1,
  has_original: true,
};
const shared = {
  ...personal,
  id: "shared-kb",
  name: "Shared library",
  shared: true,
};
const sharedGuide = {
  ...guide,
  id: "shared-guide",
  kb_id: shared.id,
  filename: "shared.txt",
};
const capability: KnowledgeCapability = {
  feature_enabled: true,
  backend: "onnx",
  selected_model: "",
  provider_id: "",
  prerequisites_ok: true,
  usable: true,
  checks: {
    model_selected: true,
    model_downloaded: true,
    deps_available: true,
    provider_ready: true,
  },
  ocr: {
    enabled: false,
    backend: "onnx",
    model: "",
    provider_id: "",
    prerequisites_ok: true,
    usable: false,
    checks: { deps_available: true, provider_ready: true },
  },
};

beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
  vi.spyOn(knowledgeBasesApi, "list").mockResolvedValue([personal]);
  vi.spyOn(knowledgeBasesApi, "get").mockResolvedValue(personal);
  vi.spyOn(knowledgeBasesApi, "getCapability").mockResolvedValue(capability);
  vi.spyOn(knowledgeBasesApi, "listDocuments").mockResolvedValue([guide]);
  vi.spyOn(knowledgeBasesApi, "citationStatus").mockResolvedValue({
    withdrawn: [],
  });
  vi.spyOn(knowledgeBasesApi, "getTextDocument").mockResolvedValue({
    id: guide.id,
    filename: guide.filename,
    content_type: guide.content_type,
    text: "Original content",
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ConfigProvider theme={{ token: { motion: false } }}>
        <App>
          <KnowledgeBasesPage />
        </App>
      </ConfigProvider>
    </MemoryRouter>,
  );
}

function switchToShared() {
  fireEvent.keyDown(
    screen.getByRole("radio", { name: "knowledgeBases.allLibraries" }),
    { key: "ArrowLeft" },
  );
  expect(
    screen.getByRole("radio", { name: "knowledgeBases.sharedLibrary" }),
  ).toBeChecked();
}

it("shows no personal detail or actions when the shared scope has no libraries", async () => {
  renderPage();
  expect(await screen.findByText(guide.filename)).toBeVisible();

  switchToShared();

  expect(await screen.findByText("knowledgeBases.selectBase")).toBeVisible();
  expect(screen.getByText("knowledgeBases.empty")).toBeVisible();
  expect(screen.queryByText(guide.filename)).not.toBeInTheDocument();
});

it("discards an unfiltered detail response that finishes after a scope change", async () => {
  let resolveDetail!: (value: KnowledgeBase) => void;
  vi.mocked(knowledgeBasesApi.get).mockReturnValueOnce(
    new Promise((resolve) => {
      resolveDetail = resolve;
    }),
  );
  renderPage();
  await waitFor(() => expect(knowledgeBasesApi.get).toHaveBeenCalled());

  switchToShared();
  await act(async () => resolveDetail(personal));

  expect(screen.getByText("knowledgeBases.selectBase")).toBeVisible();
  expect(screen.queryByText(guide.filename)).not.toBeInTheDocument();
});

it("keeps the shared range selected after refreshing its last library out of the list", async () => {
  const list = vi.mocked(knowledgeBasesApi.list);
  list.mockResolvedValue([personal, shared]);
  vi.mocked(knowledgeBasesApi.get).mockImplementation(async (id) =>
    id === shared.id ? shared : personal,
  );
  vi.mocked(knowledgeBasesApi.listDocuments).mockImplementation(async (id) =>
    id === shared.id ? [sharedGuide] : [guide],
  );
  renderPage();
  expect(await screen.findByText(guide.filename)).toBeVisible();

  switchToShared();
  expect(await screen.findByText(sharedGuide.filename)).toBeVisible();
  expect(screen.queryByText(guide.filename)).not.toBeInTheDocument();

  list.mockResolvedValue([personal]);
  fireEvent.click(screen.getByRole("button", { name: "common.refresh" }));

  expect(await screen.findByText("knowledgeBases.selectBase")).toBeVisible();
  expect(screen.getByText("knowledgeBases.empty")).toBeVisible();
  expect(screen.queryByText(sharedGuide.filename)).not.toBeInTheDocument();
  expect(screen.queryByText(guide.filename)).not.toBeInTheDocument();
});

it("removes a library from the shared range when its detail reports it has become private", async () => {
  let current = shared;
  vi.mocked(knowledgeBasesApi.list).mockResolvedValue([personal, shared]);
  vi.mocked(knowledgeBasesApi.get).mockImplementation(async (id) =>
    id === shared.id ? current : personal,
  );
  vi.mocked(knowledgeBasesApi.listDocuments).mockImplementation(async (id) =>
    id === shared.id ? [sharedGuide] : [guide],
  );
  renderPage();
  expect(await screen.findByText(guide.filename)).toBeVisible();
  switchToShared();
  expect(await screen.findByText(sharedGuide.filename)).toBeVisible();

  current = { ...shared, shared: false };
  fireEvent.click(screen.getByRole("button", { name: "common.refresh" }));

  expect(await screen.findByText("knowledgeBases.empty")).toBeVisible();
  expect(screen.queryByText(shared.name)).not.toBeInTheDocument();
  expect(screen.queryByText(sharedGuide.filename)).not.toBeInTheDocument();
});

it("keeps the mobile list usable when switching to and from an empty range", async () => {
  const matchMedia = vi.mocked(window.matchMedia).getMockImplementation()!;
  vi.mocked(window.matchMedia).mockImplementation((query) => ({
    ...matchMedia(query),
    matches: query === "(max-width: 767px)",
  }));
  renderPage();
  fireEvent.click(await screen.findByText(personal.name));
  expect(await screen.findByText(guide.filename)).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: "knowledgeBases.backToList" }),
  );

  switchToShared();
  expect(screen.getByText("knowledgeBases.empty")).toBeVisible();
  expect(screen.queryByText(guide.filename)).not.toBeInTheDocument();

  fireEvent.keyDown(
    screen.getByRole("radio", { name: "knowledgeBases.sharedLibrary" }),
    { key: "ArrowRight" },
  );
  fireEvent.click(screen.getByText(personal.name));
  expect(await screen.findByText(guide.filename)).toBeVisible();
});

it("closes a ready original preview when the document is withdrawn elsewhere", async () => {
  let withdrawn = false;
  vi.mocked(knowledgeBasesApi.citationStatus).mockImplementation(
    async (refs) => ({
      withdrawn: withdrawn ? refs : [],
    }),
  );
  vi.mocked(knowledgeBasesApi.listDocuments).mockImplementation(async () =>
    withdrawn ? [] : [guide],
  );
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", {
      name: "knowledgeBases.previewDocument",
    }),
  );
  await waitFor(() =>
    expect(screen.getByText("Original content")).toBeVisible(),
  );
  expect(screen.getByRole("dialog")).toHaveTextContent(
    "knowledgeBases.downloadOriginal",
  );

  withdrawn = true;
  fireEvent.focus(window);

  await waitFor(() => {
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("Original content")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "knowledgeBases.downloadOriginal" }),
    ).not.toBeInTheDocument();
  });
});

it("leaves a new preview open after a stale withdrawal response and a transient network failure", async () => {
  const nextDoc = { ...guide, id: "next-doc", filename: "next.txt" };
  vi.mocked(knowledgeBasesApi.listDocuments).mockResolvedValue([
    guide,
    nextDoc,
  ]);
  vi.mocked(knowledgeBasesApi.getTextDocument)
    .mockResolvedValueOnce({
      id: guide.id,
      filename: guide.filename,
      content_type: guide.content_type,
      text: "First original",
    })
    .mockResolvedValueOnce({
      id: nextDoc.id,
      filename: nextDoc.filename,
      content_type: nextDoc.content_type,
      text: "Second original",
    });
  let resolveFirst!: (
    value: Awaited<ReturnType<typeof knowledgeBasesApi.citationStatus>>,
  ) => void;
  const status = vi.mocked(knowledgeBasesApi.citationStatus);
  status.mockReturnValueOnce(
    new Promise((resolve) => {
      resolveFirst = resolve;
    }),
  );
  renderPage();
  const [first] = await screen.findAllByRole("button", {
    name: "knowledgeBases.previewDocument",
  });
  fireEvent.click(first);
  await waitFor(() => expect(screen.getByText("First original")).toBeVisible());
  fireEvent.click(
    screen.getByRole("button", { name: "knowledgeBases.previewNext" }),
  );
  expect(await screen.findByText("Second original")).toBeVisible();

  await act(async () =>
    resolveFirst({ withdrawn: [{ kb_id: personal.id, doc_id: guide.id }] }),
  );
  expect(screen.getByText("Second original")).toBeVisible();

  status.mockRejectedValueOnce(new Error("Temporary network failure"));
  await act(async () => fireEvent.focus(window));
  expect(screen.getByText("Second original")).toBeVisible();
  expect(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "knowledgeBases.downloadOriginal",
    }),
  ).toBeVisible();

  status.mockResolvedValueOnce({
    withdrawn: [{ kb_id: personal.id, doc_id: nextDoc.id }],
  });
  fireEvent(document, new Event("visibilitychange"));
  await waitFor(() =>
    expect(screen.queryByText("Second original")).not.toBeInTheDocument(),
  );
});

it("checks a preview periodically without waiting for the window to regain focus", async () => {
  renderPage();
  const button = await screen.findByRole("button", {
    name: "knowledgeBases.previewDocument",
  });
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  fireEvent.click(button);
  expect(await screen.findByText("Original content")).toBeInTheDocument();

  vi.mocked(knowledgeBasesApi.citationStatus).mockResolvedValue({
    withdrawn: [{ kb_id: personal.id, doc_id: guide.id }],
  });
  vi.mocked(knowledgeBasesApi.listDocuments).mockResolvedValue([]);
  await act(async () => vi.advanceTimersByTime(30_000));

  await waitFor(() =>
    expect(screen.queryByText("Original content")).not.toBeInTheDocument(),
  );
});
