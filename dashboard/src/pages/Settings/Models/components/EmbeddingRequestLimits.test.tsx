import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeBasesApi } from "../../../../api/modules/knowledgeBases";
import { EmbeddingRequestLimits } from "./EmbeddingRequestLimits";

vi.mock("../../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: { getCapability: vi.fn(), setFeature: vi.fn() },
}));

const capability = {
  feature_enabled: false,
  backend: "remote",
  selected_model: "embed-internal",
  provider_id: "7",
  embedding_batch_size: 3,
  embedding_max_input_chars: 1200,
};

describe("EmbeddingRequestLimits", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(knowledgeBasesApi.getCapability).mockResolvedValue(
      capability as never,
    );
    vi.mocked(knowledgeBasesApi.setFeature).mockResolvedValue(
      capability as never,
    );
  });

  it("loads saved limits and saves edits without enabling knowledge bases", async () => {
    render(
      <App>
        <EmbeddingRequestLimits />
      </App>,
    );
    const batch = await screen.findByLabelText("models.embeddingBatchSize");
    await waitFor(() => expect(batch).toHaveValue("3"));
    const user = userEvent.setup();
    await user.clear(batch);
    await user.type(batch, "2");
    await user.click(
      screen.getByRole("button", { name: "models.embeddingLimitsSave" }),
    );
    await waitFor(() =>
      expect(knowledgeBasesApi.setFeature).toHaveBeenCalledWith({
        enabled: false,
        backend: "remote",
        model: "embed-internal",
        provider_id: "7",
        embedding_batch_size: 2,
        embedding_max_input_chars: 1200,
      }),
    );
  });

  it("shows a failed load and does not allow replacing unknown saved limits", async () => {
    vi.mocked(knowledgeBasesApi.getCapability).mockRejectedValue(
      new Error("unavailable"),
    );
    render(
      <App>
        <EmbeddingRequestLimits />
      </App>,
    );
    expect(
      await screen.findByText("models.embeddingLimitsLoadFailed"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "models.embeddingLimitsSave" }),
    ).toBeDisabled();
    expect(knowledgeBasesApi.setFeature).not.toHaveBeenCalled();
  });
});
