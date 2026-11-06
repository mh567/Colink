import { request } from "../request";

export type DeviceStatus = "pending" | "approved" | "revoked";

export interface DeviceView {
  device_id: string;
  owner_user_id: number;
  owner_username: string;
  name: string;
  platform: string;
  status: DeviceStatus;
  online: boolean;
  last_seen: number | null;
  created_at: number;
}

export const devicesApi = {
  list: () => request<DeviceView[]>("/devices"),
  manage: () => request<DeviceView[]>("/devices/manage"),
  approve: (id: string) =>
    request<DeviceView>(`/devices/${encodeURIComponent(id)}/approve`, {
      method: "POST",
    }),
  revoke: (id: string) =>
    request<DeviceView>(`/devices/${encodeURIComponent(id)}/revoke`, {
      method: "POST",
    }),
};
