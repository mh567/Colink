import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { knowledgeBasesApi } from "../../../api/modules/knowledgeBases";
import { KnowledgeCitationsStrip } from "./KnowledgeCitationsStrip";
import { KnowledgeCitationStatusProvider } from "./KnowledgeCitationStatusContext";

vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: { citationStatus: vi.fn() },
}));

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
