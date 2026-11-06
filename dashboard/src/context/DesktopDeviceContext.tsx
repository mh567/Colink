import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useCurrentUser } from "../hooks/useCurrentUser";
import { isDesktopShell } from "../utils/desktopChrome";
import {
  connectDesktopDevice,
  DEVICE_STATUS_EVENT,
  disconnectDesktopDevice,
  type NativeDeviceEvent,
} from "../utils/desktopDevice";

interface DesktopDeviceState {
  available: boolean;
  event: NativeDeviceEvent | null;
  busy: "connect" | "disconnect" | null;
  connect: () => void;
  disconnect: () => void;
}

const DesktopDeviceContext = createContext<DesktopDeviceState>({
  available: false,
  event: null,
  busy: null,
  connect: () => undefined,
  disconnect: () => undefined,
});

export function DesktopDeviceProvider({ children }: { children: ReactNode }) {
  const userId = useCurrentUser()?.id;
  const [event, setEvent] = useState<NativeDeviceEvent | null>(null);
  const [busy, setBusy] = useState<DesktopDeviceState["busy"]>(null);
  const accepting = useRef(false);

  useEffect(() => {
    setEvent(null);
    setBusy(null);
    accepting.current = false;
    const onStatus = (raw: Event) => {
      if (!accepting.current) return;
      const detail = (raw as CustomEvent<NativeDeviceEvent>).detail;
      if (!detail || !["status", "error", "disconnected"].includes(detail.type))
        return;
      setEvent(detail);
      setBusy(null);
      if (detail.type === "disconnected") accepting.current = false;
    };
    window.addEventListener(DEVICE_STATUS_EVENT, onStatus);
    window.addEventListener("pagehide", disconnectDesktopDevice);
    return () => {
      accepting.current = false;
      window.removeEventListener(DEVICE_STATUS_EVENT, onStatus);
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
