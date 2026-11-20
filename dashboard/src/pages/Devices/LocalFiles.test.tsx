import { Suspense } from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { routeConfigs } from "../../routes";

vi.mock("../../context/DesktopDeviceContext", () => ({
  useDesktopDevice: () => ({
    available: true,
    event: {
      type: "status",
      status: "approved",
      online: true,
      device_id: "mine",
    },
  }),
}));

vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("../../api/modules/localExperts", () => ({
  localExpertsApi: { list: vi.fn().mockResolvedValue([]) },
}));

afterEach(() => {
  delete (window as Window & { _wails?: unknown })._wails;
});

it("shows authorized output on the Files route and previews it locally", async () => {
  const invoke = vi.fn();
  (window as Window & { _wails?: unknown })._wails = { invoke };
  const files = routeConfigs.find(({ path }) => path === "/files")!;
  await import("./index");
  await act(async () => {
    render(
      <MemoryRouter initialEntries={["/files"]}>
        <Suspense>
          <Routes>
            <Route path={files.path} element={files.element} />
            <Route path="/chat" element={<h1>Chat</h1>} />
          </Routes>
        </Suspense>
      </MemoryRouter>,
    );
  });
  expect(screen.getByRole("heading", { name: "nav.files" })).toBeVisible();
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:docx-status", {
        detail: {
          device_id: "mine",
          busy: false,
          grants: [{ id: "rw", path: "/outputs", mode: "rw" }],
          tasks: [
            {
              id: "receipt",
              output: "/outputs/report.txt",
              status: "completed",
            },
            {
              id: "other",
              output: "/elsewhere/other.txt",
              status: "completed",
            },
          ],
        },
      }),
    );
  });
  expect(screen.queryByText("/elsewhere/other.txt")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("成果目录")).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "localDocx.previewArtifact" }),
  );
  const request = invoke.mock.calls.at(-1)?.[0] as string;
  expect(request).toMatch(/^colink:artifact-preview:receipt:[0-9a-f]{32}$/);
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("colink:artifact-preview", {
        detail: {
          device_id: "mine",
          request_id: request.split(":").at(-1),
          data: btoa("Task output"),
        },
      }),
    );
  });
  expect(screen.getByText("Task output")).toBeVisible();
});
