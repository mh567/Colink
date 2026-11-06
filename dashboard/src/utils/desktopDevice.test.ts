import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { connectDesktopDevice } from "./desktopDevice";

type DesktopWindow = Window & {
  _wails?: { invoke: (message: string) => void };
};

beforeEach(() => {
  vi.stubGlobal("BASE_URL", "/colink");
  localStorage.setItem("auth_token", "employee-token");
});

afterEach(() => {
  delete (window as DesktopWindow)._wails;
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it.each([
  { apiBase: "/colink", publicBase: "/" },
  { apiBase: "", publicBase: "/colink/" },
])(
  "passes the deployed API path to native: $apiBase, $publicBase",
  ({ apiBase, publicBase }) => {
    vi.stubGlobal("BASE_URL", apiBase);
    vi.stubEnv("BASE_URL", publicBase);
    const invoke = vi.fn();
    (window as DesktopWindow)._wails = { invoke };
    connectDesktopDevice();
    const message = invoke.mock.calls[0][0] as string;
    const payload = JSON.parse(
      decodeURIComponent(
        message.slice("wails:event:emit:desktop:device-connect:".length),
      ),
    );
    expect(payload).toEqual({
      access_token: "employee-token",
      api_path: "/colink/api/devices/connect",
    });
  },
);
