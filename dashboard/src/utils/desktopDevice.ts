import { getAuthToken } from "../api/request";
import type { DeviceStatus } from "../api/modules/devices";
import { getApiUrl } from "../api/config";

type DesktopWindow = Window & {
  _wails?: { invoke?: (message: string) => void };
};

export type NativeDeviceEvent =
  | { type: "status"; device_id: string; status: DeviceStatus; online: boolean }
  | { type: "error"; code: string; message: string }
  | { type: "disconnected"; online?: false };

export const DEVICE_STATUS_EVENT = "colink:device-status";

export function connectDesktopDevice(): void {
  (window as DesktopWindow)._wails?.invoke?.(
    `wails:event:emit:desktop:device-connect:${encodeURIComponent(
      JSON.stringify({
        access_token: getAuthToken(),
        api_path: new URL(getApiUrl("/devices/connect"), window.location.href)
          .pathname,
      }),
    )}`,
  );
}

export function disconnectDesktopDevice(): void {
  (window as DesktopWindow)._wails?.invoke?.(
    "wails:event:emit:desktop:device-disconnect",
  );
}

export function refreshDesktopDeviceToken(): void {
  (window as DesktopWindow)._wails?.invoke?.(
    `wails:event:emit:desktop:device-refresh:${encodeURIComponent(
      JSON.stringify({ access_token: getAuthToken() }),
    )}`,
  );
}
