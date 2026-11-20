import { lazy, Suspense, useEffect, useState } from "react";
import { Alert, Spin } from "antd";
import { useTranslation } from "react-i18next";
import { getDocKind } from "../../utils/docKind";

const DocumentPreview = lazy(
  () => import("../../components/DocumentPreviewCore"),
);
const eventName = "colink:artifact-preview";

type Reply = {
  device_id: string;
  request_id: string;
  data?: string;
  error?: string;
};

export default function LocalArtifactPreview({
  deviceId,
  taskId,
  filename,
}: {
  deviceId: string;
  taskId: string;
  filename: string;
}) {
  const { t } = useTranslation();
  const [contents, setContents] = useState<{
    blob: Blob;
    text?: string;
  } | null>(null);
  const [error, setError] = useState("");
  const kind = getDocKind(filename);

  useEffect(() => {
    const invoke = (
      window as Window & {
        _wails?: { invoke?: (message: string) => void };
      }
    )._wails?.invoke;
    const requestId = Array.from(
      crypto.getRandomValues(new Uint8Array(16)),
      (n) => n.toString(16).padStart(2, "0"),
    ).join("");
    const unavailable = () => setError("ARTIFACT_PREVIEW_UNAVAILABLE");
    const timer = window.setTimeout(unavailable, 15_000);
    const receive = (event: Event) => {
      const reply = (event as CustomEvent<Reply>).detail;
      if (reply?.request_id !== requestId || reply.device_id !== deviceId)
        return;
      window.clearTimeout(timer);
      window.removeEventListener(eventName, receive);
      if (reply.error || typeof reply.data !== "string") {
        setError(
          reply.error === "ARTIFACT_PREVIEW_TOO_LARGE"
            ? reply.error
            : "ARTIFACT_PREVIEW_UNAVAILABLE",
        );
        return;
      }
      try {
        const bytes = Uint8Array.from(atob(reply.data), (char) =>
          char.charCodeAt(0),
        );
        setContents({
          blob: new Blob([bytes]),
          text: kind ? undefined : new TextDecoder().decode(bytes),
        });
      } catch {
        unavailable();
      }
    };
    window.addEventListener(eventName, receive);
    try {
      if (invoke) invoke(`${eventName}:${taskId}:${requestId}`);
      else unavailable();
    } catch {
      unavailable();
    }
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener(eventName, receive);
    };
  }, [deviceId, taskId, kind]);

  if (error)
    return (
      <Alert
        type="warning"
        showIcon
        message={t(`localDocx.previewErrors.${error}`)}
      />
    );
  if (!contents) return <Spin />;
  if (!kind)
    return (
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
        {contents.text}
      </pre>
    );
  return (
    <Suspense fallback={<Spin />}>
      <DocumentPreview
        kind={kind}
        filename={filename}
        fetchBlob={async () => contents.blob}
      />
    </Suspense>
  );
}
