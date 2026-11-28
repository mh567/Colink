import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, ClipboardEvent, DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { uploadFile } from "../../../api/modules/upload";
import { agentAttachmentAccessUrl } from "../../../utils/toolMediaBlocks";
import type { ChatAttachment } from "./useChat";
import { message as antMessage } from "@/utils/antdMessage";
import { apiErrorMessage } from "../../../utils/apiError";

import { inferAttachmentKind } from "../utils/chatAttachments";
import {
  DESKTOP_FILE_DROP_EVENT,
  filesFromDesktopDrop,
  type DesktopFileDropDetail,
} from "../../../utils/desktopFileDrop";
import {
  dragHasFiles,
  filesFromSnapshot,
  MAX_CHAT_UPLOAD_FILES,
  snapshotDroppedEntries,
  type CollectedDrop,
} from "../utils/droppedFiles";
import { useServerUploadLimit } from "../../../hooks/useServerUploadLimit";

export function useChatAttachments(
  agentId: string | null | undefined,
  {
    threadId,
    confirmUpload,
  }: {
    threadId?: string | null;
    confirmUpload: (files: File[]) => Promise<boolean>;
  },
) {
  const { t } = useTranslation();
  const { maxUploadBytes, maxUploadMb } = useServerUploadLimit();
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const scopeKey = `${agentId ?? ""}:${threadId ?? ""}`;
  const currentScope = useRef(scopeKey);
  currentScope.current = scopeKey;
  const revision = useRef(0);
  const pending = useRef(false);

  const clearAttachments = useCallback(() => {
    revision.current += 1;
    pending.current = false;
    setUploading(false);
    setAttachments([]);
  }, []);

  useEffect(() => {
    clearAttachments();
    return () => {
      revision.current += 1;
      pending.current = false;
    };
  }, [scopeKey, clearAttachments]);

  const processFiles = useCallback(
    async (files: FileList | File[]) => {
      if (pending.current || currentScope.current !== scopeKey) return;
      const incoming = Array.from(files);
      const capped =
        incoming.length > MAX_CHAT_UPLOAD_FILES
          ? incoming.slice(0, MAX_CHAT_UPLOAD_FILES)
          : incoming;
      if (incoming.length > MAX_CHAT_UPLOAD_FILES) {
        antMessage.warning(
          t(
            "upload.tooMany",
            "Only the first {{max}} files can be uploaded at once",
            { max: MAX_CHAT_UPLOAD_FILES },
          ),
        );
      }
      const fileArr = capped.filter((f) => {
        if (f.size > maxUploadBytes) {
          antMessage.error(
            t("upload.tooLarge", "File too large (max {{maxMb}}MB): {{name}}", {
              name: f.name,
              maxMb: maxUploadMb,
            }),
          );
          return false;
        }
        return true;
      });

      if (fileArr.length === 0) return;

      if (!agentId) {
        antMessage.error(t("upload.failed", "Upload failed"));
        return;
      }

      const startedAt = revision.current;
      const isCurrent = () =>
        startedAt === revision.current && currentScope.current === scopeKey;
      pending.current = true;
      setUploading(true);
      try {
        if (!(await confirmUpload(fileArr)) || !isCurrent()) return;
        const results = await Promise.all(
          fileArr.map(async (file) => {
            const res = await uploadFile(agentId, file);
            const workspacePath = res.path || res.workspace_path;
            const previewUrl =
              res.access_url ||
              res.url ||
              (workspacePath
                ? agentAttachmentAccessUrl(
                    agentId,
                    workspacePath,
                    res.media_type,
                  )
                : "");
            return {
              url: previewUrl,
              filename: res.filename,
              mediaType: res.media_type,
              workspacePath,
              kind: inferAttachmentKind(file, res.media_type),
            } satisfies ChatAttachment;
          }),
        );
        if (isCurrent()) setAttachments((prev) => [...prev, ...results]);
      } catch (err: unknown) {
        if (isCurrent()) {
          antMessage.error(
            apiErrorMessage(err, t("upload.failed", "Upload failed"), t),
          );
        }
      } finally {
        if (isCurrent()) {
          pending.current = false;
          setUploading(false);
        }
      }
    },
    [agentId, scopeKey, confirmUpload, maxUploadBytes, maxUploadMb, t],
  );

  const handleFileSelect = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFolderSelect = useCallback(() => {
    const input = folderInputRef.current;
    if (!input) return;
    input.setAttribute("webkitdirectory", "");
    input.click();
  }, []);

  const handleFileChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) {
        void processFiles(e.target.files);
      }
      e.target.value = "";
    },
    [processFiles],
  );

  const removeAttachment = useCallback((index: number) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const restoreAttachments = useCallback((next: ChatAttachment[]) => {
    setAttachments(next.map((a) => ({ ...a })));
  }, []);

  const handlePaste = useCallback(
    (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const pastedFiles: File[] = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === "file") {
          const file = item.getAsFile();
          if (file) pastedFiles.push(file);
        }
      }
      if (pastedFiles.length > 0) {
        e.preventDefault();
        void processFiles(pastedFiles);
      }
    },
    [processFiles],
  );

  const handleDragEnter = useCallback((e: DragEvent) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent<HTMLElement>) => {
    const next = e.relatedTarget;
    if (next instanceof Node && e.currentTarget.contains(next)) return;
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const handleDragOver = useCallback((e: DragEvent) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "copy";
    setDragOver(true);
  }, []);

  const handleDrop = useCallback(
    (e: DragEvent) => {
      const snapshot = snapshotDroppedEntries(e.dataTransfer);
      if (!snapshot.hadFileItems) return;
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
      const startedAt = revision.current;
      return (async () => {
        let collected: CollectedDrop;
        try {
          collected = await filesFromSnapshot(snapshot);
        } catch {
          antMessage.error(t("upload.failed", "Upload failed"));
          return;
        }
        if (startedAt !== revision.current) return;
        if (collected.truncated) {
          antMessage.warning(
            t(
              "upload.tooMany",
              "Only the first {{max}} files can be uploaded at once",
              { max: MAX_CHAT_UPLOAD_FILES },
            ),
          );
        }
        if (collected.files.length === 0) {
          antMessage.warning(t("upload.emptyDrop", "No files to upload"));
          return;
        }
        await processFiles(collected.files);
      })();
    },
    [processFiles, t],
  );

  useEffect(() => {
    const onDesktopDrop = (event: Event) => {
      const detail = (event as CustomEvent<DesktopFileDropDetail>).detail;
      const startedAt = revision.current;
      setDragOver(false);
      void (async () => {
        if (detail?.truncated) {
          antMessage.warning(
            t(
              "upload.tooMany",
              "Only the first {{max}} files can be uploaded at once",
              { max: detail.max ?? 500 },
            ),
          );
        }
        try {
          const files = await filesFromDesktopDrop(detail);
          if (startedAt !== revision.current) return;
          if (files.length === 0) {
            antMessage.warning(t("upload.emptyDrop", "No files to upload"));
            return;
          }
          await processFiles(files);
        } catch {
          antMessage.error(t("upload.failed", "Upload failed"));
        }
      })();
    };
    window.addEventListener(DESKTOP_FILE_DROP_EVENT, onDesktopDrop);
    return () =>
      window.removeEventListener(DESKTOP_FILE_DROP_EVENT, onDesktopDrop);
  }, [processFiles, t]);

  return {
    attachments,
    uploading,
    dragOver,
    fileInputRef,
    folderInputRef,
    processFiles,
    handleFileSelect,
    handleFolderSelect,
    handleFileChange,
    removeAttachment,
    clearAttachments,
    restoreAttachments,
    handlePaste,
    handleDragEnter,
    handleDragLeave,
    handleDragOver,
    handleDrop,
  };
}
