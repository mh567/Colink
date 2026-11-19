import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { knowledgeBasesApi } from "../../../api/modules/knowledgeBases";
import { KnowledgeCitationsStrip } from "./KnowledgeCitationsStrip";

vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: { listDocuments: vi.fn() },
}));

it("marks a removed source as withdrawn in an earlier answer", async () => {
  vi.mocked(knowledgeBasesApi.listDocuments).mockResolvedValue([]);
  render(
    <KnowledgeCitationsStrip
      citations={[
        { kbId: "kb", kbName: "Private", docId: "doc", filename: "sample.txt" },
      ]}
    />,
  );

  expect(await screen.findByText("chat.citationWithdrawn")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /sample.txt/ })).toBeDisabled();
});
