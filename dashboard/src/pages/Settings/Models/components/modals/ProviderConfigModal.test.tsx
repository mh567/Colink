import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { beforeEach, expect, it, vi } from "vitest";
import { ProviderConfigModal } from "./ProviderConfigModal";
import { request } from "../../../../../api/request";

vi.mock("../../../../../api/request", () => ({ request: vi.fn() }));

beforeEach(() => {
  vi.mocked(request).mockReset().mockResolvedValue({ ok: true });
});

function showProvider() {
  return render(
    <App>
      <ProviderConfigModal
        open
        provider={{
          id: 1,
          name: "internal",
          kind: "openai",
          base_url: "http://models.internal/v1",
          api_key_set: true,
          enabled: true,
          stream_usage: true,
          note: null,
          models: [{ id: "chat", name: "Chat", enabled: true }],
        }}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        apiPrefix="/admin/providers"
      />
    </App>,
  );
}

it("requires saving a cleared URL before testing", async () => {
  const user = userEvent.setup();
  showProvider();
  await user.clear(screen.getByLabelText("Base URL"));
  await user.click(
    screen.getAllByRole("button", { name: "models.testConnection" })[0],
  );
  expect(
    await screen.findByText("models.probeSaveChanges"),
  ).toBeInTheDocument();
  expect(
    vi.mocked(request).mock.calls.some(([url]) => url.endsWith("/test")),
  ).toBe(false);
});

it("requires saving Stream Usage changes before testing", async () => {
  const user = userEvent.setup();
  showProvider();
  await user.click(
    screen.getByRole("button", { name: "models.compatibility" }),
  );
  await user.click(screen.getByRole("switch", { name: "models.streamUsage" }));
  await user.click(
    screen.getAllByRole("button", { name: "models.testConnection" })[0],
  );
  expect(
    await screen.findByText("models.probeSaveChanges"),
  ).toBeInTheDocument();
  expect(
    vi.mocked(request).mock.calls.some(([url]) => url.endsWith("/test")),
  ).toBe(false);
});

it("clears the last test result when the form changes", async () => {
  const user = userEvent.setup();
  showProvider();
  await user.click(
    screen.getAllByRole("button", { name: "models.testConnection" })[0],
  );
  expect(await screen.findByRole("status")).toHaveTextContent(
    "models.probePassed",
  );
  await user.type(screen.getByLabelText("Base URL"), "/new");
  await waitFor(() =>
    expect(screen.queryByRole("status")).not.toBeInTheDocument(),
  );
});
