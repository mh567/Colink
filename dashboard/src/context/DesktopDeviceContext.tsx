import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { AUTH_TOKEN_RENEWED_EVENT } from "../api/request";
import { isDesktopShell } from "../utils/desktopChrome";
import {
  connectDesktopDevice,
  DEVICE_STATUS_EVENT,
  disconnectDesktopDevice,
  refreshDesktopDeviceToken,
  type NativeDeviceEvent,
} from "../utils/desktopDevice";

interface DesktopDeviceState {
  available: boolean;
  event: NativeDeviceEvent | null;
  lastApprovedDeviceId: string | null;
  busy: "connect" | "disconnect" | null;
  connect: () => void;
  disconnect: () => void;
}

const DesktopDeviceContext = createContext<DesktopDeviceState>({
  available: false,
  event: null,
  lastApprovedDeviceId: null,
  busy: null,
  connect: () => undefined,
  disconnect: () => undefined,
});

export function DesktopDeviceProvider({ children }: { children: ReactNode }) {
  const userId = useCurrentUser()?.id;
  const [event, setEvent] = useState<NativeDeviceEvent | null>(null);
  const [lastApprovedDevice, setLastApprovedDevice] = useState<{
    userId: number;
    deviceId: string;
  } | null>(null);
  const [busy, setBusy] = useState<DesktopDeviceState["busy"]>(null);
  const accepting = useRef(false);

  useEffect(() => {
    setEvent(null);
    setLastApprovedDevice(null);
    setBusy(null);
    accepting.current = false;
    const onStatus = (raw: Event) => {
      if (!accepting.current) return;
      const detail = (raw as CustomEvent<NativeDeviceEvent>).detail;
      if (!detail || !["status", "error", "disconnected"].includes(detail.type))
        return;
      setEvent(detail);
      if (
        userId != null &&
        detail.type === "status" &&
        detail.status === "approved" &&
        detail.online
      ) {
        setLastApprovedDevice({ userId, deviceId: detail.device_id });
      }
      setBusy(null);
      if (detail.type === "disconnected") accepting.current = false;
    };
    const onRenewedToken = () => {
      if (accepting.current) refreshDesktopDeviceToken();
    };
    window.addEventListener(DEVICE_STATUS_EVENT, onStatus);
    window.addEventListener(AUTH_TOKEN_RENEWED_EVENT, onRenewedToken);
    window.addEventListener("pagehide", disconnectDesktopDevice);
    return () => {
      accepting.current = false;
      window.removeEventListener(DEVICE_STATUS_EVENT, onStatus);
      window.removeEventListener(AUTH_TOKEN_RENEWED_EVENT, onRenewedToken);
      window.removeEventListener("pagehide", disconnectDesktopDevice);
      disconnectDesktopDevice();
    };
  }, [userId]);

  const run = (action: "connect" | "disconnect") => {
    if (userId == null || !isDesktopShell()) return;
    accepting.current = true;
    setBusy(action);
    if (action === "connect") setEvent(null);
    try {
      if (action === "connect") connectDesktopDevice();
      else disconnectDesktopDevice();
    } catch (error) {
      setEvent({
        type: "error",
        code: "DEVICE_CLIENT_ERROR",
        message: error instanceof Error ? error.message : String(error),
      });
      setBusy(null);
    }
  };

  return (
    <DesktopDeviceContext.Provider
      value={{
        available: isDesktopShell(),
        event,
        lastApprovedDeviceId:
          lastApprovedDevice && lastApprovedDevice.userId === userId
            ? lastApprovedDevice.deviceId
            : null,
        busy,
        connect: () => run("connect"),
        disconnect: () => run("disconnect"),
      }}
    >
      {children}
    </DesktopDeviceContext.Provider>
  );
}

export function useDesktopDevice(): DesktopDeviceState {
  return useContext(DesktopDeviceContext);
}
