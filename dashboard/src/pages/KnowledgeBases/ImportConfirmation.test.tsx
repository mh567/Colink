import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ImportConfirmation from "./ImportConfirmation";

it("requires confirmation before importing the selected files", async () => {
  const user = userEvent.setup();
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <ImportConfirmation
      grants={[{ id: "grant", path: "/authorized" }]}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />,
  );

  expect(screen.getByText("knowledgeBases.importScope")).toBeInTheDocument();
  await user.selectOptions(
    screen.getByLabelText("knowledgeBases.importDirectory"),
    "grant",
  );
  expect(onConfirm).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "common.cancel" }));
  expect(onCancel).toHaveBeenCalledOnce();
  expect(onConfirm).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "knowledgeBases.importConfirm" }),
  );
  expect(onConfirm).toHaveBeenCalledWith("grant");
});
