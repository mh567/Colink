import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { request } from "../../../api/request";
import AuditLogPanel from "./AuditLogPanel";

vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});
vi.mock("../../../api/request", () => ({ request: vi.fn() }));
vi.mock("../../../hooks/useIsMobile", () => ({ useIsMobile: () => true }));
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

it("filters by the full operation ID without changing the row limit", async () => {
  const api = vi.mocked(request);
  api.mockResolvedValue([]);
  const user = userEvent.setup();
  const operationId = "0123456789abcdef0123456789abcdef";
  render(<AuditLogPanel />);
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith("/admin/audit-log?limit=100"),
  );
  const field = screen.getByRole("textbox", { name: "adminAudit.requestId" });
  await user.type(field, operationId.toUpperCase());
  expect(field).toHaveValue(operationId.toUpperCase());
  await user.click(screen.getByRole("button", { name: "adminAudit.filter" }));
  await waitFor(() =>
    expect(api).toHaveBeenLastCalledWith(
      `/admin/audit-log?request_id=${operationId}&limit=100`,
    ),
  );
});
