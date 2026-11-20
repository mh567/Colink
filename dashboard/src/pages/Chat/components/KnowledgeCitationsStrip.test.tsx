import { useState, type ReactNode } from "react";
import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";

import { knowledgeBasesApi } from "../../../api/modules/knowledgeBases";
import { KnowledgeCitationsStrip } from "./KnowledgeCitationsStrip";
import { KnowledgeCitationStatusProvider } from "./KnowledgeCitationStatusContext";
import KnowledgeCitationPanelContent from "./KnowledgeCitationPanelContent";

vi.mock("../../../components/DocumentPreviewCore", () => ({
  default: () => null,
}));
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});
Object.defineProperty(Element.prototype, "scrollTo", {
  value: vi.fn(),
  configurable: true,
});

vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: {
    citationStatus: vi.fn(),
    listDocuments: vi.fn(),
    getTextDocument: vi.fn(),
  },
}));

it("closes an existing original preview after withdrawal and leaves prior answers visible", async () => {
  const citation = {
    kbId: "kb",
    docId: "preview",
    filename: "guide.txt",
    kbName: "Office",
  };
  const ref = { kb_id: citation.kbId, doc_id: citation.docId };
  const status = vi.mocked(knowledgeBasesApi.citationStatus);
  status.mockReset();
  status.mockResolvedValueOnce({ withdrawn: [] });
  status.mockResolvedValueOnce({ withdrawn: [ref] });
  vi.mocked(knowledgeBasesApi.listDocuments).mockResolvedValue([
    {
      id: ref.doc_id,
      filename: citation.filename,
      has_original: true,
    } as Awaited<ReturnType<typeof knowledgeBasesApi.listDocuments>>[number],
  ]);
  vi.mocked(knowledgeBasesApi.getTextDocument).mockResolvedValue({
    filename: citation.filename,
    text: "Original content",
  } as Awaited<ReturnType<typeof knowledgeBasesApi.getTextDocument>>);

  function Preview() {
    const [actions, setActions] = useState<ReactNode>(null);
    return (
      <>
        {actions}
        <KnowledgeCitationPanelContent
          citation={citation}
          onActionsChange={setActions}
        />
      </>
    );
  }
  render(
    <MemoryRouter>
      <KnowledgeCitationStatusProvider refsKey={JSON.stringify([ref])}>
        <p>Previous answer</p>
        <App>
          <Preview />
        </App>
      </KnowledgeCitationStatusProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("Original content")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "knowledgeBases.downloadOriginal" }),
  ).toBeVisible();

  fireEvent.focus(window);
  expect(await screen.findByText("chat.citationWithdrawn")).toBeVisible();
  expect(screen.queryByText("Original content")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "knowledgeBases.downloadOriginal" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Previous answer")).toBeVisible();
});

it("refreshes valid historical citations even when the history contains invalid status refs", async () => {
  const doc = { kb_id: "kb", doc_id: "doc" };
  const refs = [doc, { kb_id: "another", doc_id: "other" }];
  const legacy = { kb_id: "", doc_id: "legacy" };
  const invalidRefs = [
    legacy,
    { kb_id: "a".repeat(65), doc_id: "oversized-kb" },
    { kb_id: "kb", doc_id: "a".repeat(65) },
  ];
  const status = vi.mocked(knowledgeBasesApi.citationStatus);
  status.mockReset();
  status.mockResolvedValueOnce({ withdrawn: [] });
  status.mockResolvedValueOnce({ withdrawn: [doc] });
  render(
    <KnowledgeCitationStatusProvider
      refsKey={JSON.stringify([...invalidRefs, ...refs])}
    >
      <KnowledgeCitationsStrip
        citations={[
          {
            kbId: "kb",
            kbName: "Private",
            docId: "doc",
            filename: "sample.txt",
          },
        ]}
      />
      <KnowledgeCitationsStrip
        citations={[
          {
            kbId: "another",
            kbName: "Another",
            docId: "other",
            filename: "other.txt",
          },
          {
            kbId: legacy.kb_id,
            kbName: "",
            docId: legacy.doc_id,
            filename: "legacy.txt",
          },
        ]}
      />
    </KnowledgeCitationStatusProvider>,
  );

  await waitFor(() => expect(status).toHaveBeenCalledTimes(1));
  expect(status).toHaveBeenLastCalledWith(refs, expect.any(AbortSignal));
  expect(screen.getByRole("button", { name: /sample.txt/ })).toBeEnabled();
  fireEvent.focus(window);
  expect(await screen.findByText("chat.citationWithdrawn")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /sample.txt/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /other.txt/ })).toBeEnabled();
  expect(screen.getByRole("button", { name: /legacy.txt/ })).toBeEnabled();
  expect(status).toHaveBeenCalledTimes(2);
});
