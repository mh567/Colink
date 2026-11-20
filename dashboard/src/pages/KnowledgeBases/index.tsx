import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import {
  Alert,
  App,
  Button,
  Drawer,
  Dropdown,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Segmented,
  Select,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Tree,
  Typography,
} from "antd";
import type { MenuProps, TreeDataNode } from "antd";
import type { InputRef } from "antd/es/input";
import { ResizableTable } from "@/components/ResizableTable";
import {
  SharedAvatarMark,
  SharedResourceBadge,
} from "../../components/SharedResourceBadge";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  Eye,
  FilePlus,
  FileUp,
  Folder,
  FolderPlus,
  LayoutGrid,
  List as ListIcon,
  ListTree,
  Maximize2,
  MessageSquarePlus,
  Minimize2,
  MoreHorizontal,
  Pencil,
  PencilLine,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Signpost,
  Trash2,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";
import cx from "classnames";

import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useDesktopDevice } from "../../context/DesktopDeviceContext";
import { userCan } from "../../utils/permissions";
import type { OctopUser } from "../../api/modules/auth";
import {
  DEFAULT_KNOWLEDGE_LIMITS,
  knowledgeBasesApi,
  type KnowledgeBase,
  type KnowledgeCapability,
  type KnowledgeDocument,
  type KnowledgeOnnxModel,
} from "../../api/modules/knowledgeBases";
import {
  isUploadedAvatarUrl,
  KNOWLEDGE_AVATAR_PRESETS,
  ResourceAvatarPicker,
  avatarPickerStyles,
} from "../../components/AvatarPicker";
import { OctopEmptyMascot } from "../../components/EmptyState";
import DocumentPreviewCore from "../../components/DocumentPreviewCore";
import DocumentPreviewLoading from "../../components/DocumentPreviewLoading";
import Markdown from "../../components/Markdown";
import StreamSetupGuide from "../../components/StreamSetupGuide/StreamSetupGuide";
import { CopyableResourceId } from "../../components/CopyableResourceId";
import {
  ResourceVisibilityFields,
  ResourceVisibilityGate,
} from "../../components/ResourceVisibilityFields";
import { TextAssistBar } from "../../components/TextAssistBar";
import type { ResourceVisibilityMode } from "../../api/modules/userGroups";
import { useCardTableView } from "../../hooks/useCardTableView";
import { useHorizontalResize } from "../../hooks/useHorizontalResize";
import { useIsMobile } from "../../hooks/useIsMobile";
import { useListPanelCollapsed } from "../../hooks/useListPanelCollapsed";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import PageShell from "../../layouts/PageShell";
import {
  apiErrorMessage,
  isNotFoundApiError,
  parseApiError,
} from "../../utils/apiError";
import { saveBlobAsFile } from "../../utils/saveBlobAsFile";
import { createDetailRequestGate } from "../../utils/detailRequestGate";
import { getDocKind, type DocKind } from "../../utils/docKind";
import { isImeComposing } from "../../utils/ime";
import { formatBytes, formatSizeGb } from "../../utils/embeddingDownload";
import { fileTreeIconSpec } from "../../utils/fileTreeIcon";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import { stripFrontmatter } from "../../utils/markdown";
import {
  extractMarkdownOutline,
  stripMdInlineMarks,
  type MdOutlineItem,
} from "./mdOutline";
import { setPendingAttachKnowledgeBaseId } from "../Chat/utils/pendingAttachKnowledgeBase";
import skillStyles from "../Agent/Skills/index.module.less";
import { KnowledgeBaseAvatar } from "./knowledgeIcons";
import {
  buildKnowledgeFileTree,
  isDirectKnowledgeChild,
  joinKnowledgePath,
  KNOWLEDGE_DOC_TREE_ROOT,
  knowledgeBasename,
  knowledgeBreadcrumb,
  knowledgeFileNameParts,
  shouldOpenKnowledgeFolder,
  type KnowledgeFileTreeNode,
} from "./knowledgeFolder";
import { resolveKnowledgeDeepLink } from "./knowledgeDeepLink";
import {
  isKnowledgeDocumentLimitReached,
  knowledgeDocumentCap,
} from "./knowledgeLimits";
import {
  canDownloadKnowledgeOriginal,
  canPreviewKnowledgeDocument,
  canRichPreviewKnowledgeDocument,
  isEditableKnowledgeDocument,
  isKnowledgeMarkdownDocument,
} from "../../utils/knowledgeDocPreview";
import TextDocumentEditorModal, {
  type TextDocumentFormat,
} from "./TextDocumentEditorModal";
import ImportConfirmation from "./ImportConfirmation";
import styles from "./index.module.less";

type BaseFormValues = {
  name: string;
  description?: string;
  icon_name?: string;
  icon_url?: string;
  max_documents?: number;
};

type DocsViewMode = "card" | "table";
type LibraryScope = "all" | "shared" | "personal";
const EMPTY_DOCUMENTS: KnowledgeDocument[] = [];
const DOCS_VIEW_STORAGE_KEY = "octop:knowledge-bases-docs-view";

function matchesLibraryScope(base: KnowledgeBase, scope: LibraryScope) {
  return scope === "all" || base.shared === (scope === "shared");
}

function loadDocsViewMode(): DocsViewMode {
  const stored = localStorage.getItem(DOCS_VIEW_STORAGE_KEY);
  return stored === "table" ? "table" : "card";
}

function documentStatusColor(status: KnowledgeDocument["status"]) {
  if (status === "ready") return "success";
  if (status === "failed") return "error";
  if (status === "processing") return "processing";
  return "default";
}

function fileExtensionLabel(filename: string): string {
  const ext = filename.includes(".")
    ? filename.slice(filename.lastIndexOf(".") + 1)
    : "";
  return ext.trim().toUpperCase().slice(0, 5);
}

function sendNativeFileCommand(command: Record<string, string>) {
  const invoke = (
    window as Window & {
      _wails?: { invoke?: (message: string) => void };
    }
  )._wails?.invoke;
  if (!invoke) throw new Error("Desktop client unavailable");
  invoke(
    `wails:event:emit:desktop:docx:${encodeURIComponent(
      JSON.stringify(command),
    )}`,
  );
}

function canManageKnowledgeBase(
  base: Pick<KnowledgeBase, "owner_user_id">,
  user: OctopUser | null,
): boolean {
  return Boolean(user && base.owner_user_id === user.id);
}

function isKnowledgeOwner(
  base: Pick<KnowledgeBase, "owner_user_id">,
  user: OctopUser | null,
): boolean {
  return Boolean(user && base.owner_user_id === user.id);
}

function formatKnowledgeOwner(
  base: Pick<
    KnowledgeBase,
    "owner_display_name" | "owner_username" | "owner_user_id"
  >,
): string {
  const displayName = base.owner_display_name?.trim() || "";
  const username = base.owner_username?.trim() || "";
  return displayName || username || String(base.owner_user_id);
}

const FOLDER_ICON_COLOR = "#d97706";

type DocTreeNode = TreeDataNode & { path: string; isDir: boolean };

function DocumentFormatIcon({
  filename,
  size = 14,
  className,
  isDir = false,
}: {
  filename: string;
  size?: number;
  className?: string;
  isDir?: boolean;
}) {
  const { Icon, color } = isDir
    ? { Icon: Folder, color: FOLDER_ICON_COLOR }
    : fileTreeIconSpec(filename);
  return (
    <span
      className={className ?? styles.docFormatIcon}
      style={{ color, background: `${color}14` }}
      aria-hidden
    >
      <Icon size={size} strokeWidth={2} />
    </span>
  );
}

function ReadinessRow({
  label,
  ok,
  okText,
  failText,
}: {
  label: string;
  ok: boolean;
  okText: string;
  failText: string;
}) {
  return (
    <div className={styles.onnxReadinessRow}>
      <span className={styles.onnxReadinessLabel}>{label}</span>
      {ok ? (
        <Check size={13} color="var(--fn-color-success)" />
      ) : (
        <X size={13} color="var(--fn-color-warning)" />
      )}
      <Typography.Text type={ok ? "success" : "warning"}>
        {ok ? okText : failText}
      </Typography.Text>
    </div>
  );
}

export default function KnowledgeBasesPage() {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkConsumedRef = useRef<string | null>(null);
  const isMobile = useIsMobile();
  const timeZone = useServerTimezone();
  const user = useCurrentUser();
  const desktop = useDesktopDevice();
  const deviceId =
    desktop.event?.type === "status" &&
    desktop.event.online &&
    desktop.event.status === "approved"
      ? desktop.event.device_id
      : null;
  const canConfigureKb = userCan(user, "knowledge_settings");
  const { viewMode, setViewMode, showCardView } = useCardTableView(
    loadDocsViewMode(),
  );
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [libraryScope, setLibraryScope] = useState<LibraryScope>("all");
  const listedBases = bases.filter((base) =>
    matchesLibraryScope(base, libraryScope),
  );
  const [preferredBaseId, setPreferredBaseId] = useState<string | null>(null);
  const detailTarget =
    listedBases.find((base) => base.id === preferredBaseId) ??
    (isMobile ? undefined : listedBases[0]);
  const selectedId = detailTarget?.id;
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const [detail, setDetail] = useState<{
    base: KnowledgeBase;
    documents: KnowledgeDocument[];
  } | null>(null);
  const activeDetail =
    detail &&
    detail.base.id === selectedId &&
    matchesLibraryScope(detail.base, libraryScope)
      ? detail
      : null;
  const selected = activeDetail?.base ?? null;
  const loadedBaseId = selected?.id;
  const documents = activeDetail?.documents ?? EMPTY_DOCUMENTS;
  const [currentFolder, setCurrentFolder] = useState("");
  const [docTreeExpanded, setDocTreeExpanded] = useState<string[]>([
    KNOWLEDGE_DOC_TREE_ROOT,
  ]);
  const [folderModalOpen, setFolderModalOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [renameModalOpen, setRenameModalOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<KnowledgeDocument | null>(
    null,
  );
  const [renameName, setRenameName] = useState("");
  const [capability, setCapability] = useState<KnowledgeCapability | null>(
    null,
  );
  const [catalog, setCatalog] = useState<KnowledgeOnnxModel[]>([]);
  const [onnxDownloading, setOnnxDownloading] = useState(false);
  const [downloadProgressOpen, setDownloadProgressOpen] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState(0);
  const [downloadProgressLabel, setDownloadProgressLabel] = useState("");
  const [downloadProgressModel, setDownloadProgressModel] = useState("");
  const onnxDownloadTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [remoteProviders, setRemoteProviders] = useState<
    {
      provider_id: string;
      provider_name: string;
      models: { id: string; name: string }[];
    }[]
  >([]);
  const [loading, setLoading] = useState(true);
  const [loadingDetailId, setLoadingDetailId] = useState<string>();
  const detailLoading = Boolean(selectedId && loadingDetailId === selectedId);
  const [refreshing, setRefreshing] = useState(false);
  const [mobilePane, setMobilePane] = useState<"list" | "detail">("list");
  const [baseDrawerOpen, setBaseDrawerOpen] = useState(false);
  const [editingBaseId, setEditingBaseId] = useState<string | null>(null);
  const [featureModalOpen, setFeatureModalOpen] = useState(false);
  const [onnxProbe, setOnnxProbe] = useState<{
    ok: boolean;
    latency_ms?: number | null;
    dim?: number | null;
    error?: string | null;
  } | null>(null);
  const [onnxProbing, setOnnxProbing] = useState(false);
  const [featureEnabledDraft, setFeatureEnabledDraft] = useState(false);
  const [featureSaving, setFeatureSaving] = useState(false);
  const [featureModel, setFeatureModel] = useState<string>();
  const [featureBackend, setFeatureBackend] = useState<"onnx" | "remote">(
    "onnx",
  );
  const [featureProviderId, setFeatureProviderId] = useState<string>();
  const [featureOptionsLoading, setFeatureOptionsLoading] = useState(false);
  const [ocrEnabledDraft, setOcrEnabledDraft] = useState(false);
  const [ocrBackend, setOcrBackend] = useState<"onnx" | "remote">("onnx");
  const [ocrModel, setOcrModel] = useState<string>();
  const [ocrProviderId, setOcrProviderId] = useState<string>();
  const [ocrRemoteProviders, setOcrRemoteProviders] = useState<
    {
      provider_id: string;
      provider_name: string;
      models: { id: string; name: string }[];
    }[]
  >([]);
  const [onnxExpanded, setOnnxExpanded] = useState(false);
  const [onnxExpanding, setOnnxExpanding] = useState(false);
  const onnxExpandedRef = useRef(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewFilename, setPreviewFilename] = useState("");
  const [previewText, setPreviewText] = useState("");
  const [previewKind, setPreviewKind] = useState<DocKind | null>(null);
  const [previewDocId, setPreviewDocId] = useState<string | null>(null);
  const [previewHasOriginal, setPreviewHasOriginal] = useState(false);
  const [previewAsMarkdown, setPreviewAsMarkdown] = useState(false);
  const [previewMode, setPreviewMode] = useState<"file" | "chunks">("file");
  const [previewFileAvailable, setPreviewFileAvailable] = useState(true);
  const [chunkItems, setChunkItems] = useState<
    { ordinal: number; text: string }[]
  >([]);
  const [chunkLoading, setChunkLoading] = useState(false);
  const [chunkError, setChunkError] = useState<string | null>(null);
  const [previewMaximized, setPreviewMaximized] = useState(false);
  const [mdOutlineOpen, setMdOutlineOpen] = useState(true);
  const [mdActiveOutlineIndex, setMdActiveOutlineIndex] = useState(0);
  const [mdFindOpen, setMdFindOpen] = useState(false);
  const [mdFindQuery, setMdFindQuery] = useState("");
  const [mdFindIndex, setMdFindIndex] = useState(0);
  const [mdFindHitCount, setMdFindHitCount] = useState(0);
  const mdBodyRef = useRef<HTMLDivElement | null>(null);
  const mdFindInputRef = useRef<InputRef | null>(null);
  const mdFindHitsRef = useRef<{ node: Text; start: number }[]>([]);
  const previewAbortRef = useRef<AbortController | null>(null);
  const chunkRequestRef = useRef(0);
  const isRichPreview = Boolean(previewKind || previewAsMarkdown);

  const [textEditorOpen, setTextEditorOpen] = useState(false);
  const [textEditorMode, setTextEditorMode] = useState<"create" | "edit">(
    "create",
  );
  const [textEditorLoading, setTextEditorLoading] = useState(false);
  const [textEditorSaving, setTextEditorSaving] = useState(false);
  const [textEditorDocId, setTextEditorDocId] = useState<string | null>(null);
  const [textEditorName, setTextEditorName] = useState("");
  const [textEditorFormat, setTextEditorFormat] =
    useState<TextDocumentFormat>("md");
  const [textEditorContent, setTextEditorContent] = useState("");

  const previewableDocs = useMemo(
    () =>
      documents.filter(
        (document) =>
          !document.is_dir &&
          document.status !== "withdrawn" &&
          canPreviewKnowledgeDocument(document),
      ),
    [documents],
  );
  const previewNavIndex = useMemo(
    () =>
      previewDocId
        ? previewableDocs.findIndex((document) => document.id === previewDocId)
        : -1,
    [previewDocId, previewableDocs],
  );

  const mdOutlineItems = useMemo(
    () =>
      previewAsMarkdown && previewText.trim()
        ? extractMarkdownOutline(stripFrontmatter(previewText))
        : [],
    [previewAsMarkdown, previewText],
  );

  type MdFindHit = { node: Text; start: number };

  const collectMdFindHits = useCallback((host: HTMLElement, q: string) => {
    const needle = q.trim().toLowerCase();
    if (!needle) return [] as MdFindHit[];
    const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
    const hits: MdFindHit[] = [];
    let node = walker.nextNode();
    while (node) {
      const text = node.textContent ?? "";
      const lower = text.toLowerCase();
      let from = 0;
      while (from < lower.length) {
        const at = lower.indexOf(needle, from);
        if (at < 0) break;
        hits.push({ node: node as Text, start: at });
        from = at + Math.max(1, needle.length);
      }
      node = walker.nextNode();
    }
    return hits;
  }, []);

  // Count + jump both use the rendered DOM so the counter stays honest.
  useEffect(() => {
    if (!mdFindOpen || !previewAsMarkdown || previewLoading) {
      mdFindHitsRef.current = [];
      setMdFindHitCount(0);
      return;
    }
    const q = mdFindQuery.trim();
    if (!q) {
      mdFindHitsRef.current = [];
      setMdFindHitCount(0);
      setMdFindIndex(0);
      return;
    }
    let cancelled = false;
    const sync = () => {
      if (cancelled) return;
      const host = mdBodyRef.current;
      if (!host) {
        mdFindHitsRef.current = [];
        setMdFindHitCount(0);
        return;
      }
      const hits = collectMdFindHits(host, q);
      mdFindHitsRef.current = hits;
      setMdFindHitCount(hits.length);
      setMdFindIndex((prev) =>
        hits.length === 0 ? 0 : Math.min(prev, hits.length - 1),
      );
    };
    const raf = window.requestAnimationFrame(sync);
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf);
    };
  }, [
    mdFindOpen,
    mdFindQuery,
    previewAsMarkdown,
    previewText,
    previewLoading,
    collectMdFindHits,
  ]);

  const scrollToMdHeading = useCallback((item: MdOutlineItem) => {
    const host = mdBodyRef.current;
    if (!host) return;
    const headings = Array.from(host.querySelectorAll("h1,h2,h3,h4,h5,h6")).map(
      (heading) => ({
        el: heading,
        text: stripMdInlineMarks(heading.textContent ?? ""),
      }),
    );
    const byOccurrence = headings.filter((h) => h.text === item.title);
    const target =
      byOccurrence[item.occurrence] ??
      byOccurrence[0] ??
      // Titles containing escaped code spans can differ from the rendered
      // heading text — fall back to a prefix match.
      headings.find((h) => h.text.startsWith(item.title.slice(0, 8)));
    target?.el.scrollIntoView({ behavior: "auto", block: "start" });
  }, []);

  // Markdown outline scroll-spy: highlight the last heading above the fold.
  useEffect(() => {
    if (!previewAsMarkdown || mdOutlineItems.length === 0) {
      setMdActiveOutlineIndex(0);
      return;
    }
    const host = mdBodyRef.current;
    if (!host) return;
    const update = () => {
      const headings = Array.from(
        host.querySelectorAll<HTMLElement>("h1,h2,h3,h4,h5,h6"),
      );
      if (headings.length === 0) return;
      const top = host.scrollTop + 12;
      let active = 0;
      for (let i = 0; i < headings.length; i += 1) {
        if (headings[i].offsetTop <= top) active = i;
        else break;
      }
      setMdActiveOutlineIndex(Math.min(active, mdOutlineItems.length - 1));
    };
    update();
    host.addEventListener("scroll", update, { passive: true });
    return () => host.removeEventListener("scroll", update);
  }, [previewAsMarkdown, previewText, mdOutlineItems.length]);

  const jumpMdFind = useCallback(
    (direction: 1 | -1) => {
      const host = mdBodyRef.current;
      const q = mdFindQuery.trim();
      if (!host || !q) return;
      const hits = collectMdFindHits(host, q);
      mdFindHitsRef.current = hits;
      setMdFindHitCount(hits.length);
      if (hits.length === 0) {
        setMdFindIndex(0);
        return;
      }
      const next =
        (((mdFindIndex + direction) % hits.length) + hits.length) % hits.length;
      setMdFindIndex(next);
      const hit = hits[next];
      const el = hit.node.parentElement;
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      try {
        const range = document.createRange();
        const end = Math.min(
          hit.start + q.length,
          hit.node.textContent?.length ?? hit.start,
        );
        range.setStart(hit.node, hit.start);
        range.setEnd(hit.node, end);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
      } catch {
        /* ignore selection failures in odd text nodes */
      }
      el.classList.add(styles.mdFindFlash);
      window.setTimeout(() => el.classList.remove(styles.mdFindFlash), 900);
    },
    [mdFindIndex, mdFindQuery, collectMdFindHits],
  );

  const closePreview = useCallback(() => {
    previewAbortRef.current?.abort();
    previewAbortRef.current = null;
    setPreviewOpen(false);
    setPreviewLoading(false);
    setPreviewFilename("");
    setPreviewText("");
    setPreviewKind(null);
    setPreviewDocId(null);
    setPreviewHasOriginal(false);
    setPreviewAsMarkdown(false);
    setPreviewMode("file");
    setPreviewFileAvailable(true);
    chunkRequestRef.current += 1;
    setChunkItems([]);
    setChunkLoading(false);
    setChunkError(null);
    setPreviewMaximized(false);
    setMdOutlineOpen(true);
    setMdFindOpen(false);
    setMdFindQuery("");
    setMdFindIndex(0);
    if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => {});
    }
  }, []);

  const requestClosePreview = useCallback(() => {
    // Esc / mask: exit CSS maximized first, then close (native FS Esc is
    // handled by the browser before the dialog sees it).
    if (previewMaximized && !document.fullscreenElement) {
      setPreviewMaximized(false);
      return;
    }
    closePreview();
  }, [closePreview, previewMaximized]);

  useEffect(() => {
    if (
      previewDocId &&
      (!selected ||
        !documents.some(
          (document) =>
            document.id === previewDocId && document.status !== "withdrawn",
        ))
    )
      closePreview();
  }, [previewDocId, documents, selected, closePreview]);

  /**
   * Native fullscreen on the document content area (viewer toolbar + PDF /
   * bookmarks) — no dialog chrome around it; the browser's own "press Esc to
   * exit" hint covers leaving. Falls back to the CSS maximized dialog when
   * the Fullscreen API is unavailable or rejected (e.g. embedded contexts).
   */
  const togglePreviewFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => {});
      return;
    }
    if (previewMaximized) {
      // CSS-fallback maximized — restore the regular dialog.
      setPreviewMaximized(false);
      return;
    }
    const fsEl = (document.querySelector<HTMLElement>(
      "[data-preview-fullscreen-root]",
    ) ?? document.querySelector<HTMLElement>(".octop-modal-content")) as
      | (HTMLElement & {
          webkitRequestFullscreen?: () => Promise<void> | void;
        })
      | null;
    const request =
      fsEl?.requestFullscreen?.bind(fsEl) ?? fsEl?.webkitRequestFullscreen;
    if (!fsEl || !request) {
      setPreviewMaximized(true);
      return;
    }
    void Promise.resolve(request.call(fsEl)).then(
      () => {
        // fullscreenchange flips previewMaximized on success.
      },
      () => setPreviewMaximized(true),
    );
  }, [previewMaximized]);

  // Keep the button state in sync when the browser exits fullscreen (Esc).
  useEffect(() => {
    if (!previewOpen) return;
    const sync = () => setPreviewMaximized(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
    return () => {
      document.removeEventListener("fullscreenchange", sync);
      document.removeEventListener("webkitfullscreenchange", sync);
    };
  }, [previewOpen]);

  // Cmd/Ctrl+F opens the markdown find bar while the preview is open.
  useEffect(() => {
    if (!previewOpen || !previewAsMarkdown) return;
    const onKey = (event: KeyboardEvent) => {
      if (
        !(event.metaKey || event.ctrlKey) ||
        event.key.toLowerCase() !== "f"
      ) {
        return;
      }
      event.preventDefault();
      setMdFindOpen(true);
      window.setTimeout(() => mdFindInputRef.current?.focus(), 0);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [previewOpen, previewAsMarkdown]);
  const [baseForm] = Form.useForm<BaseFormValues>();
  const iconName = Form.useWatch("icon_name", baseForm);
  const iconUrl = Form.useWatch("icon_url", baseForm);
  const [pendingAvatarFile, setPendingAvatarFile] = useState<File | null>(null);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [defaultOpenChecked, setDefaultOpenChecked] = useState(false);
  const [baseVisibility, setBaseVisibility] =
    useState<ResourceVisibilityMode>("owner");
  const [baseGroupIds, setBaseGroupIds] = useState<string[]>([]);
  const [pendingImport, setPendingImport] = useState<{
    baseId: string;
    folder: string;
  } | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importGrants, setImportGrants] = useState<
    { id: string; path: string }[]
  >([]);
  const activeImport = useRef<{
    baseId: string;
    deviceId: string;
    requestId: string;
  } | null>(null);
  const detailRequestGate = useRef(createDetailRequestGate());
  const {
    size: sidebarWidth,
    isResizing,
    onResizeStart,
  } = useHorizontalResize({
    min: 220,
    max: 480,
    defaultSize: 280,
    storageKey: "octop:knowledge-bases:sidebar-width",
  });
  const { collapsed: listPanelCollapsed, toggle: toggleListPanel } =
    useListPanelCollapsed("octop:knowledge-bases:list-collapsed");
  const { collapsed: docTreeCollapsed, toggle: toggleDocTree } =
    useListPanelCollapsed("octop:knowledge-bases:doc-tree-collapsed", {
      defaultCollapsed: true,
    });
  const { size: docTreeWidth, onResizeStart: onDocTreeResizeStart } =
    useHorizontalResize({
      min: 180,
      max: 420,
      defaultSize: 220,
      storageKey: "octop:knowledge-bases:doc-tree-width",
    });

  const canManageSelected = Boolean(
    selected && canManageKnowledgeBase(selected, user),
  );
  const canWriteSelected = canManageSelected;
  const usable = Boolean(capability?.usable);
  const limits = capability?.limits ?? DEFAULT_KNOWLEDGE_LIMITS;
  const ownedBaseCount = user
    ? bases.filter((base) => base.owner_user_id === user.id).length
    : 0;
  const atBaseLimit = ownedBaseCount >= limits.max_bases_per_owner;
  const fileCount = documents.filter((document) => !document.is_dir).length;
  const documentCap = knowledgeDocumentCap(
    selected?.max_documents,
    limits.max_docs_per_kb,
  );
  const isAtDocumentLimit = isKnowledgeDocumentLimitReached(
    fileCount,
    documentCap,
  );
  const folderEntries = documents
    .filter((document) =>
      isDirectKnowledgeChild(document.path || document.filename, currentFolder),
    )
    .sort((a, b) => {
      const dirDelta = Number(Boolean(b.is_dir)) - Number(Boolean(a.is_dir));
      if (dirDelta !== 0) return dirDelta;
      return knowledgeBasename(a.path || a.filename).localeCompare(
        knowledgeBasename(b.path || b.filename),
      );
    });
  const folderCrumbs = knowledgeBreadcrumb(
    currentFolder,
    selected?.name || t("knowledgeBases.documents"),
  );
  const docTreeData = useMemo(() => {
    const toAntd = (node: KnowledgeFileTreeNode): DocTreeNode => ({
      key: node.path,
      path: node.path,
      isDir: true,
      isLeaf: !node.children?.length,
      title: (
        <span className={styles.docTreeNode}>
          <Folder size={13} className={styles.docTreeFolderIcon} aria-hidden />
          <span className={styles.docTreeName} title={node.name}>
            {node.name}
          </span>
        </span>
      ),
      children: node.children?.length ? node.children.map(toAntd) : undefined,
    });
    const root: DocTreeNode = {
      key: KNOWLEDGE_DOC_TREE_ROOT,
      path: "",
      isDir: true,
      isLeaf: false,
      title: (
        <span className={styles.docTreeNode}>
          <Folder size={13} className={styles.docTreeFolderIcon} aria-hidden />
          <span className={styles.docTreeName}>
            {t("knowledgeBases.documents")}
          </span>
        </span>
      ),
      children: buildKnowledgeFileTree(documents).map(toAntd),
    };
    return [root];
  }, [documents, t]);

  const loadBases = useCallback(async () => {
    try {
      const rows = await knowledgeBasesApi.list();
      setBases(rows);
    } catch (error) {
      message.error(apiErrorMessage(error, t("knowledgeBases.loadFailed"), t));
    }
  }, [message, t]);

  const loadCapability = useCallback(async () => {
    try {
      const nextCapability = await knowledgeBasesApi.getCapability();
      setCapability(nextCapability);
      setFeatureModel(nextCapability.selected_model || undefined);
      setFeatureBackend(nextCapability.backend);
      setFeatureProviderId(nextCapability.provider_id || undefined);
      setFeatureEnabledDraft(Boolean(nextCapability.feature_enabled));
      setOcrEnabledDraft(Boolean(nextCapability.ocr?.enabled));
      setOcrBackend(nextCapability.ocr?.backend ?? "onnx");
      setOcrModel(nextCapability.ocr?.model || "rapidocr");
      setOcrProviderId(nextCapability.ocr?.provider_id || undefined);
    } catch (error) {
      message.error(apiErrorMessage(error, t("knowledgeBases.loadFailed"), t));
    }
  }, [t]);

  const loadEmbeddingOptions = useCallback(
    async (allOnnx = false) => {
      if (allOnnx) {
        setOnnxExpanding(true);
      } else {
        setFeatureOptionsLoading(true);
      }
      try {
        const options = await knowledgeBasesApi.getEmbeddingOptions({
          allOnnx,
        });
        setCatalog(options.onnx);
        setRemoteProviders(options.remote);
        if (allOnnx) {
          onnxExpandedRef.current = true;
          setOnnxExpanded(true);
        }
        return options;
      } catch (error) {
        message.error(
          apiErrorMessage(error, t("knowledgeBases.loadFailed"), t),
        );
        return undefined;
      } finally {
        setFeatureOptionsLoading(false);
        setOnnxExpanding(false);
      }
    },
    [t],
  );

  const loadOcrOptions = useCallback(async () => {
    try {
      const options = await knowledgeBasesApi.getOcrOptions();
      setOcrRemoteProviders(options.remote);
      return options;
    } catch (error) {
      message.error(apiErrorMessage(error, t("knowledgeBases.loadFailed"), t));
      return undefined;
    }
  }, [t]);

  const stopOnnxDownloadWatch = useCallback(() => {
    if (onnxDownloadTimer.current) {
      clearInterval(onnxDownloadTimer.current);
      onnxDownloadTimer.current = null;
    }
  }, []);

  const applyOnnxDownloadProgress = useCallback(
    (modelId: string, status: string, progress: number) => {
      const pct = Math.max(0, Math.min(100, Math.round((progress || 0) * 100)));
      setDownloadProgress(pct);
      if (status === "loading") {
        setDownloadProgressLabel(
          t("models.onnxDownloadLoading", { model: modelId }),
        );
      } else if (status === "downloading") {
        setDownloadProgressLabel(
          t("models.onnxDownloading", { model: modelId }),
        );
      } else {
        setDownloadProgressLabel(t("models.localDownloadPreparing"));
      }
    },
    [t],
  );

  const finishOnnxDownload = useCallback(
    async (modelId: string, status: string, error?: string | null) => {
      stopOnnxDownloadWatch();
      setOnnxDownloading(false);
      setDownloadProgressOpen(false);
      await loadEmbeddingOptions(onnxExpandedRef.current);
      if (status === "done") {
        try {
          await knowledgeBasesApi.activateOnnx(modelId);
          message.success(t("knowledgeBases.onnxServiceEnabled"));
        } catch (activateError) {
          message.success(t("models.onnxDownloadDone", { model: modelId }));
          message.warning(
            apiErrorMessage(
              activateError,
              t("knowledgeBases.featureSaveFailed"),
              t,
            ),
          );
        }
        return;
      }
      message.error(error || t("models.onnxDownloadFailed"));
    },
    [loadEmbeddingOptions, stopOnnxDownloadWatch, t],
  );

  const watchOnnxDownloadStatus = useCallback(
    (modelId: string) => {
      stopOnnxDownloadWatch();
      let inFlight = false;
      let stopped = false;
      const tick = async () => {
        if (inFlight || stopped) return;
        inFlight = true;
        try {
          const state = await knowledgeBasesApi.getOnnxDownloadStatus();
          applyOnnxDownloadProgress(
            state.model_name || modelId,
            state.status,
            state.progress,
          );
          if (state.status === "done" || state.status === "failed") {
            stopped = true;
            stopOnnxDownloadWatch();
            await finishOnnxDownload(
              state.model_name || modelId,
              state.status,
              state.error,
            );
          }
        } catch (error) {
          stopped = true;
          stopOnnxDownloadWatch();
          await finishOnnxDownload(
            modelId,
            "failed",
            error instanceof Error ? error.message : String(error),
          );
        } finally {
          inFlight = false;
        }
      };
      onnxDownloadTimer.current = setInterval(() => {
        void tick();
      }, 500);
      void tick();
    },
    [applyOnnxDownloadProgress, finishOnnxDownload, stopOnnxDownloadWatch],
  );

  useEffect(() => () => stopOnnxDownloadWatch(), [stopOnnxDownloadWatch]);

  const loadDetail = useCallback(
    async (id: string, options?: { silent?: boolean }) => {
      if (selectedIdRef.current !== id) return;
      const requestId = detailRequestGate.current.begin();
      const isCurrent = () =>
        selectedIdRef.current === id &&
        detailRequestGate.current.isCurrent(requestId);
      if (!options?.silent) setLoadingDetailId(id);
      try {
        const [base, nextDocuments] = await Promise.all([
          knowledgeBasesApi.get(id),
          knowledgeBasesApi.listDocuments(id),
        ]);
        if (!isCurrent()) return;
        setBases((current) => {
          if (
            !current.some((row) => row.id === id && row.shared !== base.shared)
          )
            return current;
          return current.map((row) => (row.id === id ? base : row));
        });
        setDetail({ base, documents: nextDocuments });
      } catch (error) {
        if (!isCurrent()) return;
        if (
          isNotFoundApiError(error) ||
          parseApiError(error)?.code === "KNOWLEDGE_FORBIDDEN"
        ) {
          setDetail(null);
          setBases((current) => current.filter((row) => row.id !== id));
          return;
        }
        if (!options?.silent) {
          message.error(
            apiErrorMessage(error, t("knowledgeBases.loadFailed"), t),
          );
        }
      } finally {
        if (isCurrent()) setLoadingDetailId(undefined);
      }
    },
    [message, t],
  );

  useEffect(() => {
    if (!previewOpen || !loadedBaseId || !previewDocId) return;
    const preview = previewAbortRef.current;
    const ref = { kb_id: loadedBaseId, doc_id: previewDocId };
    let pending: AbortController | undefined;
    const recheck = () => {
      if (document.visibilityState === "hidden") return;
      pending?.abort();
      const request = (pending = new AbortController());
      void knowledgeBasesApi.citationStatus([ref], request.signal).then(
        ({ withdrawn }) => {
          if (
            request.signal.aborted ||
            preview !== previewAbortRef.current ||
            selectedIdRef.current !== ref.kb_id ||
            !withdrawn.some(
              (row) => row.kb_id === ref.kb_id && row.doc_id === ref.doc_id,
            )
          ) {
            return;
          }
          closePreview();
          message.info(t("knowledgeBases.originalUnavailable"));
          setDetail((current) =>
            current?.base.id === ref.kb_id
              ? {
                  ...current,
                  documents: current.documents.filter(
                    (row) => row.id !== ref.doc_id,
                  ),
                }
              : current,
          );
          void loadDetail(ref.kb_id, { silent: true });
          void loadBases();
        },
        () => {
          // A failed check does not mean the document was withdrawn.
        },
      );
    };
    recheck();
    window.addEventListener("focus", recheck);
    document.addEventListener("visibilitychange", recheck);
    const timer = window.setInterval(recheck, 30_000);
    return () => {
      pending?.abort();
      window.removeEventListener("focus", recheck);
      document.removeEventListener("visibilitychange", recheck);
      window.clearInterval(timer);
    };
  }, [
    closePreview,
    loadBases,
    loadDetail,
    loadedBaseId,
    message,
    previewDocId,
    previewOpen,
    t,
  ]);

  useEffect(() => {
    const receive = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          device_id: string;
          grants?: { id: string; path: string }[];
          knowledge_import_request_id?: string;
          error?: string;
        }>
      ).detail;
      if (!detail || !deviceId || detail.device_id !== deviceId) return;
      setImportGrants(detail.grants ?? []);
      const active = activeImport.current;
      if (!active || detail.knowledge_import_request_id !== active.requestId)
        return;
      activeImport.current = null;
      setImportBusy(false);
      if (detail.error) {
        message.error(detail.error);
      } else {
        message.success(t("knowledgeBases.uploaded"));
        if (selectedIdRef.current === active.baseId)
          void loadDetail(active.baseId);
        void loadBases();
      }
    };
    window.addEventListener("colink:docx-status", receive);
    return () => window.removeEventListener("colink:docx-status", receive);
  }, [deviceId, loadBases, loadDetail, message, t]);

  useEffect(() => {
    if (activeImport.current && activeImport.current.deviceId !== deviceId) {
      activeImport.current = null;
      setImportBusy(false);
      message.error(t("knowledgeBases.importDeviceRequired"));
    }
  }, [deviceId, message, t]);

  useEffect(() => {
    setCurrentFolder("");
    setDocTreeExpanded([KNOWLEDGE_DOC_TREE_ROOT]);
  }, [selected?.id]);

  useEffect(() => {
    setDocTreeExpanded((prev) => {
      const keys = new Set(prev);
      keys.add(KNOWLEDGE_DOC_TREE_ROOT);
      let acc = "";
      for (const part of currentFolder.split("/").filter(Boolean)) {
        acc = acc ? `${acc}/${part}` : part;
        keys.add(acc);
      }
      const next = [...keys];
      if (next.every((key) => prev.includes(key))) return prev;
      return next;
    });
  }, [currentFolder]);

  useEffect(() => {
    void Promise.all([loadBases(), loadCapability()]).finally(() =>
      setLoading(false),
    );
  }, [loadBases, loadCapability]);

  useEffect(() => {
    const indexing = documents.some(
      (document) =>
        !document.is_dir &&
        (document.status === "pending" || document.status === "processing"),
    );
    if (!selected || !indexing) return;
    const timer = window.setInterval(() => {
      void loadDetail(selected.id, { silent: true });
    }, 2500);
    return () => window.clearInterval(timer);
  }, [documents, loadDetail, selected]);
  useEffect(() => {
    setDetail(null);
    if (!selectedId) {
      setLoadingDetailId(undefined);
      return;
    }
    setPreferredBaseId(selectedId);
    void loadDetail(selectedId);
    const gate = detailRequestGate.current;
    return () => {
      gate.begin();
    };
  }, [loadDetail, selectedId]);

  useEffect(() => {
    if (!isMobile) setMobilePane("list");
  }, [isMobile]);

  const refresh = async () => {
    setRefreshing(true);
    try {
      await Promise.all([
        loadCapability(),
        loadBases().then(() =>
          selectedId ? loadDetail(selectedId) : undefined,
        ),
      ]);
    } finally {
      setRefreshing(false);
    }
  };

  const selectBase = (base: KnowledgeBase) => {
    setPreferredBaseId(base.id);
    if (base.id === selectedId && !selected && !detailLoading) {
      void loadDetail(base.id);
    }
    if (isMobile) setMobilePane("detail");
  };

  const changeLibraryScope = (scope: LibraryScope) => {
    setLibraryScope(scope);
    const keepSelection =
      detailTarget && matchesLibraryScope(detailTarget, scope);
    setPreferredBaseId(keepSelection ? detailTarget.id : null);
    if (!keepSelection) setMobilePane("list");
  };

  const openCreate = () => {
    if (atBaseLimit) {
      message.warning(
        t("knowledgeBases.baseLimitReached", {
          count: limits.max_bases_per_owner,
        }),
      );
      return;
    }
    setPendingAvatarFile(null);
    baseForm.setFieldsValue({
      name: "",
      description: "",
      icon_name: "book-open",
      icon_url: "",
      max_documents: 100,
    });
    setDefaultOpenChecked(false);
    setBaseVisibility("owner");
    setBaseGroupIds([]);
    setEditingBaseId(null);
    setBaseDrawerOpen(true);
  };

  const openEdit = (base: KnowledgeBase) => {
    setPendingAvatarFile(null);
    baseForm.setFieldsValue({
      name: base.name,
      description: base.description,
      icon_name: base.icon_name || undefined,
      icon_url: base.icon_url || "",
      max_documents: base.max_documents,
    });
    setDefaultOpenChecked(base.default_open);
    setBaseVisibility(base.visibility ?? (base.shared ? "all" : "owner"));
    setBaseGroupIds(base.group_ids ?? []);
    setEditingBaseId(base.id);
    setBaseDrawerOpen(true);
  };

  const saveBase = async () => {
    const values = await baseForm.validateFields();
    if (baseVisibility === "groups" && baseGroupIds.length === 0) {
      message.error(t("resourceVisibility.needGroup"));
      return;
    }
    const payload = {
      ...values,
      // Blob previews are local-only; upload runs after create/update.
      icon_url: pendingAvatarFile
        ? ""
        : values.icon_url?.startsWith("blob:")
        ? ""
        : values.icon_url?.trim() || "",
      default_open: defaultOpenChecked,
      shared: baseVisibility !== "owner",
      visibility: baseVisibility,
      group_ids: baseGroupIds,
    };
    try {
      let next = editingBaseId
        ? await knowledgeBasesApi.update(editingBaseId, payload)
        : await knowledgeBasesApi.create(payload);
      if (pendingAvatarFile) {
        try {
          const uploaded = await knowledgeBasesApi.uploadAvatar(
            next.id,
            pendingAvatarFile,
          );
          next = { ...next, icon_url: uploaded.icon_url };
        } catch {
          message.warning(t("knowledgeBases.avatarUploadLater"));
        }
      }
      setBaseDrawerOpen(false);
      setPendingAvatarFile(null);
      await loadBases();
      selectBase(next);
      if (next.id === selected?.id) await loadDetail(next.id);
      message.success(
        t(editingBaseId ? "knowledgeBases.updated" : "knowledgeBases.created"),
      );
    } catch (error) {
      message.error(apiErrorMessage(error, t("knowledgeBases.saveFailed"), t));
    }
  };

  const deleteBase = async (base: KnowledgeBase) => {
    const deletedId = base.id;
    try {
      await knowledgeBasesApi.delete(deletedId);
      setBases((current) => current.filter((row) => row.id !== deletedId));
      message.success(t("knowledgeBases.deleted"));
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.deleteFailed"), t),
      );
    }
  };

  const confirmDeleteBase = (base: KnowledgeBase) => {
    modal.confirm({
      title: t("knowledgeBases.deleteConfirm"),
      okText: t("common.delete"),
      cancelText: t("common.cancel"),
      okButtonProps: { danger: true },
      onOk: () => deleteBase(base),
    });
  };

  const addBaseToChat = (base: KnowledgeBase) => {
    setPendingAttachKnowledgeBaseId(base.id);
    navigate("/chat", { state: { attachKnowledgeBaseId: base.id } });
  };

  const baseMenuItems = (base: KnowledgeBase): MenuProps["items"] => {
    const canManage = canManageKnowledgeBase(base, user);
    const items: MenuProps["items"] = [];
    if (usable) {
      items.push({
        key: "addToChat",
        icon: <MessageSquarePlus size={14} />,
        label: t("knowledgeBases.addToChat"),
        onClick: ({ domEvent }) => {
          domEvent.stopPropagation();
          addBaseToChat(base);
        },
      });
    }
    if (canManage) {
      if (items.length > 0) {
        items.push({ type: "divider" });
      }
      items.push(
        {
          key: "edit",
          icon: <Pencil size={14} />,
          label: t("common.edit"),
          onClick: ({ domEvent }) => {
            domEvent.stopPropagation();
            openEdit(base);
          },
        },
        {
          key: "delete",
          icon: <Trash2 size={14} />,
          label: t("common.delete"),
          danger: true,
          onClick: ({ domEvent }) => {
            domEvent.stopPropagation();
            confirmDeleteBase(base);
          },
        },
      );
    }
    return items;
  };

  // A probe describes one model; drop it as soon as the draft points elsewhere.
  useEffect(() => {
    setOnnxProbe(null);
  }, [featureModel, featureBackend]);

  const runOnnxProbe = async () => {
    if (!featureModel) return;
    setOnnxProbing(true);
    try {
      setOnnxProbe(await knowledgeBasesApi.testOnnx(featureModel));
    } catch (error) {
      setOnnxProbe({
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setOnnxProbing(false);
    }
  };

  const saveFeature = async (confirmed = false) => {
    if (!featureEnabledDraft) {
      setFeatureSaving(true);
      try {
        setCapability(await knowledgeBasesApi.setFeature({ enabled: false }));
        setFeatureModalOpen(false);
        message.success(t("knowledgeBases.featureDisabled"));
      } catch (error) {
        message.error(
          apiErrorMessage(error, t("knowledgeBases.featureSaveFailed"), t),
        );
      } finally {
        setFeatureSaving(false);
      }
      return;
    }
    if (!featureModel || (featureBackend === "remote" && !featureProviderId)) {
      return;
    }
    if (
      ocrEnabledDraft &&
      ocrBackend === "remote" &&
      (!ocrProviderId || !ocrModel)
    ) {
      return;
    }
    if (
      featureBackend === "onnx" &&
      !catalog.find((model) => model.id === featureModel)?.downloaded
    ) {
      message.warning(t("knowledgeBases.downloadNeedModel"));
      return;
    }
    if (
      !confirmed &&
      capability?.feature_enabled &&
      (capability.backend !== featureBackend ||
        capability.selected_model !== featureModel ||
        capability.provider_id !==
          (featureBackend === "remote" ? featureProviderId : "") ||
        capability.ocr?.enabled !== ocrEnabledDraft ||
        capability.ocr?.backend !== ocrBackend ||
        (ocrEnabledDraft &&
          ocrBackend === "remote" &&
          (capability.ocr?.model !== ocrModel ||
            capability.ocr?.provider_id !== ocrProviderId)))
    ) {
      modal.confirm({
        title: t("knowledgeBases.rebuildConfirmTitle"),
        content: t("knowledgeBases.rebuildConfirmDescription"),
        okText: t("common.confirm"),
        cancelText: t("common.cancel"),
        onOk: () => void saveFeature(true),
      });
      return;
    }
    setFeatureSaving(true);
    try {
      const next = await knowledgeBasesApi.setFeature({
        enabled: true,
        backend: featureBackend,
        model: featureModel,
        provider_id: featureBackend === "remote" ? featureProviderId : "",
        ocr_enabled: ocrEnabledDraft,
        ocr_backend: ocrBackend,
        ocr_model: ocrBackend === "remote" ? ocrModel : "rapidocr",
        ocr_provider_id: ocrBackend === "remote" ? ocrProviderId : "",
      });
      setCapability(next);
      setFeatureModalOpen(false);
      message.success(t("knowledgeBases.featureEnabled"));
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.featureSaveFailed"), t),
      );
    } finally {
      setFeatureSaving(false);
    }
  };

  const openSettings = () => {
    const selectedModel = capability?.selected_model || undefined;
    const selectedBackend = capability?.backend ?? "onnx";
    onnxExpandedRef.current = false;
    setOnnxExpanded(false);
    setFeatureEnabledDraft(Boolean(capability?.feature_enabled));
    setFeatureBackend(selectedBackend);
    setFeatureModel(selectedModel);
    setFeatureProviderId(capability?.provider_id || undefined);
    setOcrEnabledDraft(Boolean(capability?.ocr?.enabled));
    setOcrBackend(capability?.ocr?.backend ?? "onnx");
    setOcrModel(capability?.ocr?.model || "rapidocr");
    setOcrProviderId(capability?.ocr?.provider_id || undefined);
    setFeatureModalOpen(true);
    void (async () => {
      const options = await loadEmbeddingOptions(false);
      const selectedInCatalog = Boolean(
        selectedModel &&
          options?.onnx.some((model) => model.id === selectedModel),
      );
      if (selectedBackend === "onnx" && selectedModel && !selectedInCatalog) {
        await loadEmbeddingOptions(true);
      }
    })();
    void loadOcrOptions();
    void knowledgeBasesApi.getOnnxDownloadStatus().then((state) => {
      if (state.status === "downloading" || state.status === "loading") {
        const modelId = state.model_name;
        setOnnxDownloading(true);
        setDownloadProgressModel(modelId);
        setDownloadProgressOpen(true);
        applyOnnxDownloadProgress(modelId, state.status, state.progress);
        watchOnnxDownloadStatus(modelId);
      }
    });
  };

  const startOnnxDownload = async (modelId: string) => {
    const selected = catalog.find((model) => model.id === modelId);
    const size = formatSizeGb(selected?.size_gb);
    modal.confirm({
      title: t("models.localDownloadConfirmTitle"),
      content: t("models.localDownloadConfirmOnnx", {
        name: modelId,
        size: size || t("knowledgeBases.sizeUnknown"),
      }),
      okText: t("knowledgeBases.downloadModel"),
      cancelText: t("common.cancel"),
      // Return void so antd closes this confirm immediately. An async onOk
      // stays open (OK button loading) until downloadOnnx settles, and
      // modal.confirm is pinned at z-index 2000, above the progress dialog.
      onOk: () => {
        setOnnxDownloading(true);
        setDownloadProgressModel(modelId);
        setDownloadProgress(0);
        setDownloadProgressLabel(t("models.localDownloadPreparing"));
        setDownloadProgressOpen(true);
        void knowledgeBasesApi.downloadOnnx(modelId).then(
          () => {
            watchOnnxDownloadStatus(modelId);
          },
          (error: unknown) => {
            setOnnxDownloading(false);
            setDownloadProgressOpen(false);
            message.error(
              apiErrorMessage(error, t("models.onnxDownloadFailed"), t),
            );
          },
        );
      },
    });
  };

  const dismissDownloadProgressToBackground = () => {
    setDownloadProgressOpen(false);
    message.info(t("models.localDownloadBackground"));
  };

  const requestImport = () => {
    if (
      !selected ||
      selected.shared ||
      !deviceId ||
      !usable ||
      isAtDocumentLimit ||
      importBusy
    )
      return;
    try {
      setImportGrants([]);
      sendNativeFileCommand({ action: "list" });
      setPendingImport({ baseId: selected.id, folder: currentFolder });
    } catch {
      message.error(t("knowledgeBases.importDeviceRequired"));
    }
  };

  const createFolder = async () => {
    if (!selected) return;
    const name = folderName.trim();
    if (!name) return;
    try {
      await knowledgeBasesApi.createFolder(
        selected.id,
        joinKnowledgePath(currentFolder, name),
      );
      setFolderModalOpen(false);
      setFolderName("");
      await loadDetail(selected.id, { silent: true });
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.createFolderFailed"), t),
      );
    }
  };

  const openRenameFolder = (document: KnowledgeDocument) => {
    setRenameTarget(document);
    setRenameName(knowledgeBasename(document.path || document.filename));
    setRenameModalOpen(true);
  };

  const openRenameFile = (document: KnowledgeDocument) => {
    const current = knowledgeBasename(document.path || document.filename);
    setRenameTarget(document);
    setRenameName(knowledgeFileNameParts(current).stem);
    setRenameModalOpen(true);
  };

  const renameEntry = async () => {
    if (!selected || !renameTarget) return;
    const trimmed = renameName.trim();
    if (!trimmed) return;
    const current = knowledgeBasename(
      renameTarget.path || renameTarget.filename,
    );
    const next = renameTarget.is_dir
      ? trimmed
      : `${trimmed}${knowledgeFileNameParts(current).suffix}`;
    if (next === current) {
      setRenameModalOpen(false);
      return;
    }
    const renamingFile = !renameTarget.is_dir;
    try {
      await knowledgeBasesApi.renameDocument(
        selected.id,
        renameTarget.id,
        next,
      );
      setRenameModalOpen(false);
      setRenameTarget(null);
      setRenameName("");
      if (previewDocId === renameTarget.id) setPreviewFilename(next);
      await loadDetail(selected.id);
      message.success(
        t(
          renamingFile
            ? "knowledgeBases.renameFileSuccess"
            : "knowledgeBases.renameFolderSuccess",
        ),
      );
    } catch (error) {
      message.error(
        apiErrorMessage(
          error,
          t(
            renamingFile
              ? "knowledgeBases.renameFileFailed"
              : "knowledgeBases.renameFolderFailed",
          ),
          t,
        ),
      );
    }
  };

  const deleteDocument = async (documentId: string) => {
    if (!selected) return;
    try {
      await knowledgeBasesApi.deleteDocument(selected.id, documentId);
      await loadDetail(selected.id);
      await loadBases();
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.deleteFailed"), t),
      );
    }
  };

  const rebuildDocument = async (documentId: string) => {
    if (!selected) return;
    try {
      await knowledgeBasesApi.reindexDocument(selected.id, documentId);
      message.success(t("knowledgeBases.rebuildDocumentSuccess"));
      await loadDetail(selected.id);
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("knowledgeBases.rebuildDocumentFailed"), t),
      );
    }
  };

  const loadDocumentChunks = async (kbId: string, documentId: string) => {
    const request = ++chunkRequestRef.current;
    setChunkLoading(true);
    setChunkError(null);
    try {
      const body = await knowledgeBasesApi.listDocumentChunks(kbId, documentId);
      if (chunkRequestRef.current !== request) return;
      setChunkItems(body.chunks);
    } catch (error) {
      if (chunkRequestRef.current !== request) return;
      setChunkItems([]);
      setChunkError(
        apiErrorMessage(error, t("knowledgeBases.chunksFailed"), t),
      );
    } finally {
      if (chunkRequestRef.current === request) setChunkLoading(false);
    }
  };

  const openDocumentPreview = async (
    document: KnowledgeDocument,
    mode: "file" | "chunks" = "file",
  ) => {
    if (!selected || document.is_dir || document.status === "withdrawn") return;
    const filePreview = canPreviewKnowledgeDocument(document);
    const chunkPreview = (document.chunk_count ?? 0) > 0;
    if (mode === "file" && !filePreview) return;
    if (!filePreview && !chunkPreview) return;
    const rich = canRichPreviewKnowledgeDocument(document);
    const kind = rich ? getDocKind(document.filename) : null;
    const asMarkdown = isKnowledgeMarkdownDocument(document);
    previewAbortRef.current?.abort();
    const abort = new AbortController();
    previewAbortRef.current = abort;
    setPreviewOpen(true);
    setPreviewFilename(document.filename);
    setPreviewKind(kind);
    setPreviewDocId(document.id);
    setPreviewHasOriginal(canDownloadKnowledgeOriginal(document));
    setPreviewAsMarkdown(asMarkdown);
    setPreviewText("");
    setMdOutlineOpen(true);
    setMdActiveOutlineIndex(0);
    setMdFindOpen(false);
    setMdFindQuery("");
    setMdFindIndex(0);
    setMdFindHitCount(0);
    setPreviewFileAvailable(filePreview);
    setPreviewMode(filePreview ? mode : "chunks");
    chunkRequestRef.current += 1;
    setChunkItems([]);
    setChunkLoading(false);
    setChunkError(null);
    if (mode === "chunks" || !filePreview) {
      void loadDocumentChunks(selected.id, document.id);
    }
    if (!filePreview) {
      setPreviewLoading(false);
      setPreviewText("");
      return;
    }
    if (kind) {
      setPreviewLoading(false);
      return;
    }
    setPreviewLoading(true);
    try {
      // Prefer raw UTF-8 for editable text so markdown preview matches the file.
      if (asMarkdown || isEditableKnowledgeDocument(document)) {
        const payload = await knowledgeBasesApi.getTextDocument(
          selected.id,
          document.id,
        );
        if (abort.signal.aborted) return;
        setPreviewFilename(payload.filename);
        setPreviewText(
          payload.text.trim() ? payload.text : t("knowledgeBases.previewEmpty"),
        );
      } else {
        const preview = await knowledgeBasesApi.previewDocument(
          selected.id,
          document.id,
        );
        if (abort.signal.aborted) return;
        setPreviewFilename(preview.filename);
        setPreviewText(
          preview.text.trim() ? preview.text : t("knowledgeBases.previewEmpty"),
        );
      }
    } catch (error) {
      if (abort.signal.aborted) return;
      closePreview();
      message.error(
        apiErrorMessage(error, t("knowledgeBases.previewFailed"), t),
      );
    } finally {
      if (!abort.signal.aborted) setPreviewLoading(false);
    }
  };

  const downloadDocumentOriginal = async (
    documentId: string,
    filename: string,
    signal?: AbortSignal,
  ) => {
    if (!selected) return;
    try {
      const blob = await knowledgeBasesApi.fetchDocumentFile(
        selected.id,
        documentId,
        "attachment",
        undefined,
        signal,
      );
      if (signal?.aborted) return;
      await saveBlobAsFile(blob, filename || "download");
    } catch (error) {
      if (signal?.aborted) return;
      message.error(
        apiErrorMessage(error, t("knowledgeBases.downloadOriginalFailed"), t),
      );
    }
  };

  const downloadPreviewOriginal = () => {
    const signal = previewAbortRef.current?.signal;
    if (previewDocId && signal) {
      void downloadDocumentOriginal(previewDocId, previewFilename, signal);
    }
  };

  // Keep the viewer's fetch stable as indexing refreshes the base object.
  const fetchPreviewBlob = useCallback(
    async (
      onProgress?: (loaded: number, total: number) => void,
      signal?: AbortSignal,
    ) => {
      if (!loadedBaseId || !previewDocId) {
        throw new Error("missing knowledge document preview target");
      }
      return knowledgeBasesApi.fetchDocumentFile(
        loadedBaseId,
        previewDocId,
        "inline",
        onProgress,
        signal,
      );
    },
    [loadedBaseId, previewDocId],
  );

  useEffect(() => {
    const kb = searchParams.get("kb");
    const doc = searchParams.get("doc");
    if (!kb || !doc) return;
    const key = `${kb}:${doc}`;
    if (deepLinkConsumedRef.current === key) return;
    if (bases.length === 0) return;

    const clearDeepLinkParams = () => {
      const next = new URLSearchParams(searchParams);
      next.delete("kb");
      next.delete("doc");
      setSearchParams(next, { replace: true });
    };

    const base = bases.find((row) => row.id === kb);
    if (!base) {
      deepLinkConsumedRef.current = key;
      message.error(t("chat.citationDeepLinkFailed"));
      clearDeepLinkParams();
      return;
    }

    if (selectedId !== kb) {
      if (!listedBases.some((row) => row.id === kb)) setLibraryScope("all");
      selectBase(base);
      return;
    }

    if (!selected || detailLoading) return;

    const resolved = resolveKnowledgeDeepLink({
      kb,
      doc,
      bases,
      documents,
    });
    if (!resolved) {
      deepLinkConsumedRef.current = key;
      message.error(t("chat.citationDeepLinkFailed"));
      clearDeepLinkParams();
      return;
    }

    deepLinkConsumedRef.current = key;
    setCurrentFolder(resolved.folder);
    const full = documents.find((row) => row.id === resolved.document.id);
    if (full) {
      void openDocumentPreview(full);
    }
    clearDeepLinkParams();
    // selectBase / openDocumentPreview are stable-enough for this one-shot deep link;
    // including them would re-fire while loadDetail is in flight.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deep-link one-shot
  }, [
    bases,
    detailLoading,
    documents,
    message,
    searchParams,
    selected,
    selectedId,
    setSearchParams,
    t,
  ]);

  const openCreateTextDocument = () => {
    setTextEditorMode("create");
    setTextEditorDocId(null);
    setTextEditorName("");
    setTextEditorFormat("md");
    setTextEditorContent("");
    setTextEditorLoading(false);
    setTextEditorOpen(true);
  };

  const openEditTextDocument = async (document: KnowledgeDocument) => {
    if (!selected) return;
    setTextEditorMode("edit");
    setTextEditorDocId(document.id);
    setTextEditorName(document.filename);
    setTextEditorFormat(
      document.filename.toLowerCase().endsWith(".txt") ||
        document.content_type === "text/plain"
        ? "txt"
        : "md",
    );
    setTextEditorContent("");
    setTextEditorOpen(true);
    setTextEditorLoading(true);
    try {
      const payload = await knowledgeBasesApi.getTextDocument(
        selected.id,
        document.id,
      );
      setTextEditorContent(payload.text);
      setTextEditorName(payload.filename);
      setTextEditorFormat(
        payload.content_type === "text/plain" ||
          payload.filename.toLowerCase().endsWith(".txt")
          ? "txt"
          : "md",
      );
    } catch (error) {
      setTextEditorOpen(false);
      message.error(
        apiErrorMessage(error, t("knowledgeBases.editDocumentFailed"), t),
      );
    } finally {
      setTextEditorLoading(false);
    }
  };

  const saveTextDocument = async (values: {
    name: string;
    format: TextDocumentFormat;
    content: string;
  }) => {
    if (!selected) return;
    setTextEditorSaving(true);
    try {
      if (textEditorMode === "create") {
        await knowledgeBasesApi.createTextDocument(selected.id, {
          name: values.name,
          format: values.format,
          content: values.content,
          path: currentFolder || undefined,
        });
        message.success(t("knowledgeBases.createFileSuccess"));
      } else if (textEditorDocId) {
        await knowledgeBasesApi.updateTextDocument(
          selected.id,
          textEditorDocId,
          values.content,
        );
        message.success(t("knowledgeBases.editDocumentSuccess"));
      }
      setTextEditorOpen(false);
      await loadDetail(selected.id);
    } catch (error) {
      message.error(
        apiErrorMessage(
          error,
          textEditorMode === "create"
            ? t("knowledgeBases.createFileFailed")
            : t("knowledgeBases.editDocumentFailed"),
          t,
        ),
      );
    } finally {
      setTextEditorSaving(false);
    }
  };

  const renderDocumentActions = (document: KnowledgeDocument) => {
    if (document.status === "withdrawn") {
      return (
        <span className={styles.docCardActions}>
          <Typography.Text type="secondary">
            {t("knowledgeBases.originalUnavailable")}
          </Typography.Text>
          {canWriteSelected && (
            <Popconfirm
              title={t("knowledgeBases.deleteDocumentConfirm")}
              onConfirm={() => void deleteDocument(document.id)}
            >
              <Button
                type="text"
                danger
                size="small"
                icon={<Trash2 size={14} />}
                aria-label={t("common.delete")}
              />
            </Popconfirm>
          )}
        </span>
      );
    }
    const canPreview = canPreviewKnowledgeDocument(document);
    const previewBtn = (
      <Button
        type="text"
        size="small"
        icon={<Eye size={14} />}
        disabled={!canPreview}
        aria-label={t("knowledgeBases.previewDocument")}
        onClick={() => void openDocumentPreview(document)}
      />
    );
    return (
      <div
        className={styles.docCardActions}
        data-kb-doc-actions=""
        onClick={(event) => event.stopPropagation()}
      >
        {document.is_dir ? null : (
          <>
            <Tooltip
              title={
                canPreview
                  ? t("knowledgeBases.previewDocument")
                  : t("knowledgeBases.previewUnsupported")
              }
            >
              {canPreview ? previewBtn : <span>{previewBtn}</span>}
            </Tooltip>
            {canDownloadKnowledgeOriginal(document) ? (
              <Tooltip title={t("knowledgeBases.downloadOriginal")}>
                <Button
                  type="text"
                  size="small"
                  icon={<Download size={14} />}
                  aria-label={t("knowledgeBases.downloadOriginal")}
                  onClick={() =>
                    void downloadDocumentOriginal(
                      document.id,
                      document.filename,
                    )
                  }
                />
              </Tooltip>
            ) : null}
          </>
        )}
        {canWriteSelected ? (
          <>
            {!document.is_dir && isEditableKnowledgeDocument(document) ? (
              <Tooltip title={t("knowledgeBases.editDocument")}>
                <Button
                  type="text"
                  size="small"
                  icon={<Pencil size={14} />}
                  aria-label={t("knowledgeBases.editDocument")}
                  onClick={() => void openEditTextDocument(document)}
                />
              </Tooltip>
            ) : null}
            {document.is_dir ? null : (
              <Popconfirm
                title={t("knowledgeBases.rebuildDocumentConfirm")}
                onConfirm={() => void rebuildDocument(document.id)}
              >
                <Tooltip title={t("knowledgeBases.rebuildDocument")}>
                  <Button
                    type="text"
                    size="small"
                    icon={<RefreshCw size={14} />}
                    aria-label={t("knowledgeBases.rebuildDocument")}
                  />
                </Tooltip>
              </Popconfirm>
            )}
            {document.is_dir ? (
              <Tooltip title={t("knowledgeBases.renameFolder")}>
                <Button
                  type="text"
                  size="small"
                  icon={<PencilLine size={14} />}
                  aria-label={t("knowledgeBases.renameFolder")}
                  onClick={(event) => {
                    event.stopPropagation();
                    openRenameFolder(document);
                  }}
                />
              </Tooltip>
            ) : null}
            <Popconfirm
              title={
                document.is_dir
                  ? t("knowledgeBases.deleteFolderConfirm")
                  : t("knowledgeBases.deleteDocumentConfirm")
              }
              onConfirm={() => void deleteDocument(document.id)}
            >
              <Button
                type="text"
                danger
                size="small"
                icon={<Trash2 size={14} />}
                aria-label={t("common.delete")}
              />
            </Popconfirm>
          </>
        ) : null}
      </div>
    );
  };

  const renderEntryName = (document: KnowledgeDocument, nameClass?: string) => {
    const name = knowledgeBasename(document.path || document.filename);
    const ext = !document.is_dir ? fileExtensionLabel(name) : "";
    return (
      <>
        <span className={styles.filenameHover}>
          <span
            className={cx(styles.filenameText, nameClass)}
            title={document.path || name}
          >
            {name}
          </span>
          {canWriteSelected &&
          !document.is_dir &&
          document.status !== "withdrawn" ? (
            <Tooltip title={t("knowledgeBases.renameFile")}>
              <button
                type="button"
                className={styles.filenameRenameBtn}
                data-kb-doc-actions=""
                aria-label={t("knowledgeBases.renameFile")}
                onClick={(event) => {
                  event.stopPropagation();
                  openRenameFile(document);
                }}
              >
                <PencilLine size={13} strokeWidth={2} aria-hidden />
              </button>
            </Tooltip>
          ) : null}
        </span>
        {ext ? <span className={styles.docExtBadge}>{ext}</span> : null}
      </>
    );
  };

  const showDetailPane =
    !isMobile ||
    (mobilePane === "detail" && Boolean(selected || detailLoading));
  const showListPane = !isMobile || !showDetailPane;
  const showListPanel = showListPane && (isMobile || !listPanelCollapsed);
  const showEnableGuide = !loading && !usable && bases.length === 0;
  const showEmptyGuide = !loading && usable && bases.length === 0;
  const emptyLayoutClassName = styles.emptyLayout;
  const setupMascot = <OctopEmptyMascot />;

  const onDocsViewChange = (value: string | number) => {
    const mode = value === "table" ? "table" : "card";
    setViewMode(mode);
    localStorage.setItem(DOCS_VIEW_STORAGE_KEY, mode);
  };

  return (
    <PageShell
      title={t("knowledgeBases.title")}
      subtitle={t("knowledgeBases.subtitle")}
      fill
      actions={
        canConfigureKb ? (
          <Button icon={<Settings size={15} />} onClick={openSettings}>
            {t("knowledgeBases.settingsTitle")}
          </Button>
        ) : undefined
      }
    >
      {!loading && capability?.checks.vector_available === false ? (
        <Alert
          type="error"
          showIcon
          message={t("apiErrors.KNOWLEDGE_VECTOR_UNAVAILABLE")}
        />
      ) : null}
      {loading ? (
        <div className={emptyLayoutClassName}>
          <div className={styles.centered}>
            <Spin />
          </div>
        </div>
      ) : showEnableGuide ? (
        <div className={emptyLayoutClassName}>
          <StreamSetupGuide
            className={styles.emptyGuide}
            wide
            plain
            icon={setupMascot}
            title={
              canConfigureKb
                ? t("knowledgeBases.enableGuideTitle")
                : t("knowledgeBases.unavailableTitle")
            }
            description={
              canConfigureKb
                ? t("knowledgeBases.enableGuideDesc")
                : t("knowledgeBases.unavailableDescriptionNonAdmin")
            }
            steps={
              canConfigureKb
                ? [
                    {
                      label: t("knowledgeBases.enableGuideStepOpen"),
                      detail: t("knowledgeBases.enableGuideStepOpenDetail"),
                    },
                    {
                      label: t("knowledgeBases.enableGuideStepToggle"),
                      detail: t("knowledgeBases.enableGuideStepToggleDetail"),
                    },
                    {
                      label: t("knowledgeBases.enableGuideStepModel"),
                      detail: t("knowledgeBases.enableGuideStepModelDetail"),
                    },
                  ]
                : []
            }
            primaryAction={
              canConfigureKb
                ? {
                    label: t("knowledgeBases.settingsTitle"),
                    onClick: openSettings,
                    icon: <Settings size={14} />,
                  }
                : undefined
            }
          />
        </div>
      ) : showEmptyGuide ? (
        <div className={emptyLayoutClassName}>
          <StreamSetupGuide
            className={styles.emptyGuide}
            wide
            plain
            icon={setupMascot}
            title={t("knowledgeBases.emptyGuideTitle")}
            description={t("knowledgeBases.emptyGuideDesc")}
            steps={[
              {
                label: t("knowledgeBases.emptyGuideStepWhat"),
                detail: t("knowledgeBases.emptyGuideStepWhatDetail"),
              },
              {
                label: t("knowledgeBases.emptyGuideStepHow"),
                detail: t("knowledgeBases.emptyGuideStepHowDetail"),
              },
              {
                label: t("knowledgeBases.emptyGuideStepShare"),
                detail: t("knowledgeBases.emptyGuideStepShareDetail"),
              },
            ]}
            primaryAction={{
              label: t("knowledgeBases.create"),
              onClick: openCreate,
              icon: <Plus size={14} />,
              disabled: atBaseLimit,
            }}
          />
        </div>
      ) : (
        <div
          className={cx(
            styles.layout,
            isResizing && styles.layoutResizing,
            isMobile && styles.layoutMobile,
          )}
          style={
            {
              "--knowledge-bases-sidebar-width": `${sidebarWidth}px`,
            } as CSSProperties
          }
        >
          {showListPanel ? (
            <aside className={styles.baseList}>
              <div className={styles.listPanelHeader}>
                <span className={styles.listPanelTitle}>
                  {t("knowledgeBases.title")}
                </span>
              </div>
              <div className={styles.listActions}>
                <Button
                  type="primary"
                  icon={<Plus size={15} />}
                  disabled={!usable || atBaseLimit}
                  onClick={openCreate}
                >
                  {t("knowledgeBases.create")}
                </Button>
                <Tooltip title={t("common.refresh")}>
                  <Button
                    icon={<RefreshCw size={15} />}
                    aria-label={t("common.refresh")}
                    loading={refreshing}
                    onClick={() => void refresh()}
                  />
                </Tooltip>
              </div>
              <Segmented<LibraryScope>
                className={styles.libraryScope}
                aria-label={t("knowledgeBases.libraryScope")}
                size="small"
                value={libraryScope}
                onChange={changeLibraryScope}
                options={[
                  { value: "all", label: t("knowledgeBases.allLibraries") },
                  {
                    value: "personal",
                    label: t("knowledgeBases.personalLibrary"),
                  },
                  { value: "shared", label: t("knowledgeBases.sharedLibrary") },
                ]}
              />
              {loading ? (
                <div className={styles.centered}>
                  <Spin />
                </div>
              ) : (
                <List
                  className={styles.list}
                  split={false}
                  dataSource={listedBases}
                  locale={{
                    emptyText: (
                      <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description={t("knowledgeBases.empty")}
                      />
                    ),
                  }}
                  renderItem={(base) => (
                    <List.Item
                      className={styles.listRow}
                      tabIndex={0}
                      onKeyDown={(event) => {
                        if (
                          event.target !== event.currentTarget ||
                          (event.key !== "Enter" && event.key !== " ")
                        )
                          return;
                        event.preventDefault();
                        selectBase(base);
                      }}
                      onClick={() => selectBase(base)}
                    >
                      <div
                        className={cx(
                          styles.listItem,
                          base.id === selected?.id && styles.active,
                        )}
                      >
                        <div className={styles.listName}>
                          <SharedAvatarMark
                            shared={base.shared}
                            owned={isKnowledgeOwner(base, user)}
                            ownerName={formatKnowledgeOwner(base)}
                          >
                            <span className={styles.listIcon}>
                              <KnowledgeBaseAvatar
                                iconUrl={base.icon_url}
                                iconName={base.icon_name}
                                size={18}
                              />
                            </span>
                          </SharedAvatarMark>
                          <Tooltip title={base.name} placement="topLeft">
                            <span className={styles.listNameText}>
                              {base.name}
                            </span>
                          </Tooltip>
                          <SharedResourceBadge
                            shared={base.shared && isKnowledgeOwner(base, user)}
                            owned
                            ownerName={formatKnowledgeOwner(base)}
                          />
                          <div
                            className={styles.listId}
                            onClick={(event) => event.stopPropagation()}
                          >
                            <CopyableResourceId
                              inline
                              valueOnly
                              className={styles.listIdButton}
                              label={t("knowledgeBases.baseId")}
                              value={base.id}
                              copyTitle={t("knowledgeBases.copyBaseId")}
                            />
                          </div>
                          {canManageKnowledgeBase(base, user) || usable ? (
                            <Dropdown
                              menu={{ items: baseMenuItems(base) }}
                              trigger={["click"]}
                              placement="bottomRight"
                            >
                              <button
                                type="button"
                                className={styles.listMoreBtn}
                                aria-label={t("common.more")}
                                onClick={(event) => event.stopPropagation()}
                              >
                                <MoreHorizontal size={15} />
                              </button>
                            </Dropdown>
                          ) : null}
                        </div>
                        <Tooltip
                          title={
                            base.description ||
                            t("knowledgeBases.noDescription")
                          }
                          placement="topLeft"
                        >
                          <div className={styles.listDescription}>
                            {base.description ||
                              t("knowledgeBases.noDescription")}
                          </div>
                        </Tooltip>
                        <div className={styles.listMeta}>
                          <span
                            className={styles.listCreator}
                            title={t("knowledgeBases.createdBy", {
                              name: formatKnowledgeOwner(base),
                            })}
                          >
                            {t("knowledgeBases.createdBy", {
                              name: formatKnowledgeOwner(base),
                            })}
                          </span>
                          <div className={styles.listMetaEnd}>
                            <Tag className={styles.listCountTag}>
                              {t("knowledgeBases.documentCount", {
                                count: base.doc_count,
                              })}
                            </Tag>
                            {base.default_open ? (
                              <div className={styles.listMetaBadges}>
                                <span
                                  className={cx(
                                    styles.listBadge,
                                    styles.listBadgeDefaultOpen,
                                  )}
                                >
                                  {t("knowledgeBases.defaultOpenBadge")}
                                </span>
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </div>
                    </List.Item>
                  )}
                />
              )}
            </aside>
          ) : null}
          {!isMobile ? (
            <div
              data-split-divider=""
              className={cx(
                styles.splitDivider,
                listPanelCollapsed && styles.splitDividerCollapsed,
              )}
            >
              {!listPanelCollapsed ? (
                <div
                  className={styles.resizeHandle}
                  role="separator"
                  aria-orientation="vertical"
                  aria-label={t("knowledgeBases.resizeSidebar")}
                  onPointerDown={onResizeStart}
                />
              ) : null}
              <Tooltip
                title={
                  listPanelCollapsed
                    ? t("knowledgeBases.expandListPanel")
                    : t("knowledgeBases.collapseListPanel")
                }
              >
                <button
                  type="button"
                  className={styles.listPanelEdgeToggle}
                  onClick={(event) => {
                    event.stopPropagation();
                    toggleListPanel();
                  }}
                  onMouseDown={(event) => event.stopPropagation()}
                  aria-label={
                    listPanelCollapsed
                      ? t("knowledgeBases.expandListPanel")
                      : t("knowledgeBases.collapseListPanel")
                  }
                >
                  {listPanelCollapsed ? (
                    <ChevronRight size={14} strokeWidth={2} />
                  ) : (
                    <ChevronLeft size={14} strokeWidth={2} />
                  )}
                </button>
              </Tooltip>
            </div>
          ) : null}
          {showDetailPane ? (
            <section className={styles.detail}>
              {detailLoading ? (
                <div className={styles.detailLoading}>
                  <Spin />
                </div>
              ) : null}
              {!selected && !detailLoading ? (
                <div className={styles.emptyDetail}>
                  <OctopEmptyMascot size={180} />
                  <p className={styles.emptyDetailText}>
                    {t("knowledgeBases.selectBase")}
                  </p>
                </div>
              ) : !selected ? null : (
                <div className={styles.detailBody}>
                  <div
                    className={cx(skillStyles.gridToolbar, styles.docsToolbar)}
                  >
                    <div className={styles.toolbarStart}>
                      {isMobile ? (
                        <button
                          type="button"
                          className={styles.mobileBack}
                          onClick={() => setMobilePane("list")}
                          aria-label={t("knowledgeBases.backToList")}
                        >
                          <ChevronLeft size={18} />
                        </button>
                      ) : null}
                      <nav
                        className={styles.pathBreadcrumb}
                        aria-label={t("knowledgeBases.pathBreadcrumb")}
                      >
                        <Signpost
                          size={14}
                          className={styles.pathBreadcrumbIcon}
                          aria-hidden
                        />
                        {folderCrumbs.map((seg, index) => {
                          const isLast = index === folderCrumbs.length - 1;
                          return (
                            <span
                              key={`${seg.path}:${index}`}
                              className={styles.pathBreadcrumbSegment}
                            >
                              {index > 0 ? (
                                <span
                                  className={styles.pathBreadcrumbSep}
                                  aria-hidden
                                >
                                  /
                                </span>
                              ) : null}
                              {isLast ? (
                                <span
                                  className={styles.pathBreadcrumbCurrent}
                                  title={seg.label}
                                >
                                  <span className={styles.pathBreadcrumbLabel}>
                                    {seg.label}
                                  </span>
                                </span>
                              ) : (
                                <button
                                  type="button"
                                  className={styles.pathBreadcrumbLink}
                                  onClick={() => setCurrentFolder(seg.path)}
                                  title={seg.label}
                                >
                                  <span className={styles.pathBreadcrumbLabel}>
                                    {seg.label}
                                  </span>
                                </button>
                              )}
                            </span>
                          );
                        })}
                      </nav>
                      <Tag>
                        {t(
                          selected.shared
                            ? "knowledgeBases.sharedLibrary"
                            : "knowledgeBases.personalLibrary",
                        )}
                      </Tag>
                    </div>
                    <div className={skillStyles.gridToolbarRight}>
                      <Segmented
                        size="small"
                        value={viewMode}
                        onChange={onDocsViewChange}
                        options={[
                          {
                            value: "card",
                            label: (
                              <span className={skillStyles.viewModeLabel}>
                                <LayoutGrid size={14} />
                                {t("knowledgeBases.viewCard")}
                              </span>
                            ),
                          },
                          {
                            value: "table",
                            label: (
                              <span className={skillStyles.viewModeLabel}>
                                <ListIcon size={14} />
                                {t("knowledgeBases.viewTable")}
                              </span>
                            ),
                          },
                        ]}
                      />
                      {canWriteSelected ? (
                        <Button
                          icon={<FolderPlus size={14} />}
                          onClick={() => {
                            setFolderName("");
                            setFolderModalOpen(true);
                          }}
                        >
                          {t("knowledgeBases.createFolder")}
                        </Button>
                      ) : null}
                      {canWriteSelected ? (
                        <Button
                          icon={<FilePlus size={14} />}
                          disabled={isAtDocumentLimit}
                          onClick={openCreateTextDocument}
                        >
                          {t("knowledgeBases.createFile")}
                        </Button>
                      ) : null}
                      {canWriteSelected && !selected.shared ? (
                        <Tooltip
                          title={
                            !deviceId
                              ? t("knowledgeBases.importDeviceRequired")
                              : undefined
                          }
                        >
                          <Button
                            type="primary"
                            icon={<FileUp size={14} />}
                            loading={importBusy}
                            disabled={
                              !deviceId ||
                              !usable ||
                              isAtDocumentLimit ||
                              importBusy
                            }
                            onClick={requestImport}
                          >
                            {t("knowledgeBases.upload")}
                          </Button>
                        </Tooltip>
                      ) : null}
                    </div>
                  </div>
                  <div className={styles.docsPathRow}>
                    <span className={skillStyles.gridCount}>
                      {documentCap === null
                        ? t("knowledgeBases.documentCount", {
                            count: fileCount,
                          })
                        : t("knowledgeBases.documentLimit", {
                            count: fileCount,
                            max: documentCap,
                          })}
                    </span>
                    {canWriteSelected ? (
                      <Typography.Text
                        type="secondary"
                        className={styles.uploadHint}
                        ellipsis={{ tooltip: true }}
                      >
                        {t(
                          capability?.ocr?.usable
                            ? "knowledgeBases.uploadHintOcr"
                            : "knowledgeBases.uploadHint",
                          {
                            sizeMb: Math.round(
                              limits.max_document_bytes / (1024 * 1024),
                            ),
                          },
                        )}
                      </Typography.Text>
                    ) : null}
                  </div>
                  <div
                    className={cx(
                      styles.docsSplit,
                      !isMobile && styles.docsSplitFramed,
                    )}
                  >
                    {!isMobile && !docTreeCollapsed ? (
                      <aside
                        className={styles.docTreePane}
                        style={{ width: docTreeWidth }}
                        aria-label={t("knowledgeBases.docTree")}
                      >
                        <div className={styles.docTreeHeader}>
                          {t("knowledgeBases.folderTreeTitle")}
                        </div>
                        <div className={styles.docTreeScroll}>
                          <Tree
                            blockNode
                            treeData={docTreeData}
                            selectedKeys={[
                              currentFolder || KNOWLEDGE_DOC_TREE_ROOT,
                            ]}
                            expandedKeys={docTreeExpanded}
                            onExpand={(keys) =>
                              setDocTreeExpanded(keys.map(String))
                            }
                            onSelect={(_keys, info) => {
                              if (!info.selected) return;
                              const node = info.node as DocTreeNode;
                              setCurrentFolder(node.path);
                            }}
                          />
                        </div>
                      </aside>
                    ) : null}
                    {!isMobile ? (
                      <div
                        data-split-divider=""
                        className={cx(
                          styles.docTreeDivider,
                          docTreeCollapsed && styles.docTreeDividerCollapsed,
                        )}
                      >
                        {!docTreeCollapsed ? (
                          <div
                            className={styles.docTreeResize}
                            role="separator"
                            aria-orientation="vertical"
                            aria-label={t("knowledgeBases.resizeDocTree")}
                            onPointerDown={onDocTreeResizeStart}
                          />
                        ) : null}
                        <Tooltip
                          title={
                            docTreeCollapsed
                              ? t("knowledgeBases.showDocTree")
                              : t("knowledgeBases.hideDocTree")
                          }
                        >
                          <button
                            type="button"
                            className={styles.docTreeToggle}
                            onClick={(event) => {
                              event.stopPropagation();
                              toggleDocTree();
                            }}
                            onMouseDown={(event) => event.stopPropagation()}
                            aria-label={
                              docTreeCollapsed
                                ? t("knowledgeBases.showDocTree")
                                : t("knowledgeBases.hideDocTree")
                            }
                          >
                            {docTreeCollapsed ? (
                              <ChevronRight size={14} strokeWidth={2} />
                            ) : (
                              <ChevronLeft size={14} strokeWidth={2} />
                            )}
                          </button>
                        </Tooltip>
                      </div>
                    ) : null}
                    <div className={styles.docsMain}>
                      {isAtDocumentLimit ? (
                        <Alert
                          className={styles.limitAlert}
                          type="info"
                          showIcon
                          message={t("knowledgeBases.documentLimitReached", {
                            count: documentCap ?? limits.max_docs_per_kb,
                          })}
                        />
                      ) : null}
                      {folderEntries.length === 0 ? (
                        <Empty
                          image={Empty.PRESENTED_IMAGE_SIMPLE}
                          description={
                            canWriteSelected
                              ? t("knowledgeBases.emptyDocumentsDropHint")
                              : t("knowledgeBases.emptyDocuments")
                          }
                        />
                      ) : showCardView ? (
                        <div className={styles.docCardGrid}>
                          {folderEntries.map((document) => {
                            const name = knowledgeBasename(
                              document.path || document.filename,
                            );
                            return (
                              <div
                                key={document.id}
                                className={styles.docCard}
                                role={document.is_dir ? "button" : undefined}
                                onClick={(event) => {
                                  if (
                                    !shouldOpenKnowledgeFolder(
                                      Boolean(document.is_dir),
                                      event,
                                    )
                                  ) {
                                    return;
                                  }
                                  setCurrentFolder(
                                    document.path || document.filename,
                                  );
                                }}
                              >
                                <div className={styles.docCardHeader}>
                                  <DocumentFormatIcon
                                    filename={name}
                                    size={14}
                                    isDir={document.is_dir}
                                  />
                                  <div className={styles.docCardTitleBlock}>
                                    <div className={styles.docCardTitleRow}>
                                      {renderEntryName(
                                        document,
                                        styles.docCardName,
                                      )}
                                    </div>
                                    <div className={styles.docCardMeta}>
                                      {document.is_dir
                                        ? t("knowledgeBases.folder")
                                        : `${formatBytes(
                                            document.byte_size,
                                          )} · ${t(
                                            "knowledgeBases.chunkCount",
                                            {
                                              count: document.chunk_count,
                                            },
                                          )}`}
                                    </div>
                                  </div>
                                  <span
                                    data-kb-doc-actions=""
                                    onClick={(event) => event.stopPropagation()}
                                  >
                                    {renderDocumentActions(document)}
                                  </span>
                                </div>
                                {document.is_dir ? null : (
                                  <div className={styles.docCardFooter}>
                                    <Tooltip
                                      title={
                                        document.error_message ||
                                        t(
                                          `knowledgeBases.statuses.${document.status}`,
                                        )
                                      }
                                    >
                                      <Tag
                                        className={styles.docStatusTag}
                                        color={documentStatusColor(
                                          document.status,
                                        )}
                                      >
                                        {t(
                                          `knowledgeBases.statusesShort.${document.status}`,
                                        )}
                                      </Tag>
                                    </Tooltip>
                                    <span
                                      className={styles.docUpdatedAt}
                                      title={formatServerDateTime(
                                        document.updated_at,
                                        timeZone,
                                      )}
                                    >
                                      {formatServerDateTime(
                                        document.updated_at,
                                        timeZone,
                                      )}
                                    </span>
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <ResizableTable
                          storageKey="kb-documents"
                          size="small"
                          rowKey="id"
                          pagination={false}
                          scroll={{ x: 720 }}
                          dataSource={folderEntries}
                          onRow={(document) => ({
                            onClick: document.is_dir
                              ? (event) => {
                                  if (!shouldOpenKnowledgeFolder(true, event)) {
                                    return;
                                  }
                                  setCurrentFolder(
                                    document.path || document.filename,
                                  );
                                }
                              : undefined,
                          })}
                          locale={{
                            emptyText: t("knowledgeBases.emptyDocuments"),
                          }}
                          columns={[
                            {
                              title: t("knowledgeBases.filename"),
                              key: "filename",
                              ellipsis: true,
                              render: (_, document) => {
                                const name = knowledgeBasename(
                                  document.path || document.filename,
                                );
                                return (
                                  <span className={styles.tableFilename}>
                                    <DocumentFormatIcon
                                      filename={name}
                                      size={13}
                                      className={styles.docTableIcon}
                                      isDir={document.is_dir}
                                    />
                                    {renderEntryName(document)}
                                  </span>
                                );
                              },
                            },
                            {
                              title: t("knowledgeBases.entryType"),
                              key: "entry_type",
                              width: 88,
                              render: (_, document) =>
                                document.is_dir
                                  ? t("knowledgeBases.folder")
                                  : t("knowledgeBases.file"),
                            },
                            {
                              title: t("knowledgeBases.status"),
                              key: "status",
                              width: 100,
                              render: (_, document) =>
                                document.is_dir ? (
                                  "—"
                                ) : (
                                  <Tooltip
                                    title={document.error_message || undefined}
                                  >
                                    <Tag
                                      color={documentStatusColor(
                                        document.status,
                                      )}
                                    >
                                      {t(
                                        `knowledgeBases.statusesShort.${document.status}`,
                                      )}
                                    </Tag>
                                  </Tooltip>
                                ),
                            },
                            {
                              title: t("knowledgeBases.chunks"),
                              dataIndex: "chunk_count",
                              key: "chunk_count",
                              width: 80,
                              render: (chunkCount: number, document) =>
                                document.is_dir ||
                                document.status === "withdrawn" ? (
                                  "—"
                                ) : (
                                  <button
                                    type="button"
                                    className={styles.chunkCountButton}
                                    disabled={!chunkCount}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      void openDocumentPreview(
                                        document,
                                        "chunks",
                                      );
                                    }}
                                  >
                                    {chunkCount}
                                  </button>
                                ),
                            },
                            {
                              title: t("knowledgeBases.updatedAt"),
                              dataIndex: "updated_at",
                              key: "updated_at",
                              width: 170,
                              render: (updatedAt: number) =>
                                formatServerDateTime(updatedAt, timeZone),
                            },
                            {
                              title: t("common.actions"),
                              key: "actions",
                              width: canWriteSelected ? 120 : 48,
                              render: (_, document) => (
                                <div
                                  className={styles.tableActions}
                                  data-kb-doc-actions=""
                                  onClick={(event) => event.stopPropagation()}
                                >
                                  {renderDocumentActions(document)}
                                </div>
                              ),
                            },
                          ]}
                        />
                      )}
                    </div>
                  </div>
                </div>
              )}
            </section>
          ) : null}
        </div>
      )}

      <Modal
        title={
          <div className={styles.previewModalTitleRow}>
            <span
              className={styles.previewModalTitleText}
              title={previewFilename || undefined}
            >
              {previewFilename || t("knowledgeBases.previewDocument")}
            </span>
            <Segmented
              size="small"
              value={previewMode}
              onChange={(value) => {
                const next = value === "chunks" ? "chunks" : "file";
                setPreviewMode(next);
                if (
                  next === "chunks" &&
                  selected &&
                  previewDocId &&
                  chunkItems.length === 0
                ) {
                  void loadDocumentChunks(selected.id, previewDocId);
                }
              }}
              options={[
                {
                  label: t("knowledgeBases.previewFile"),
                  value: "file",
                  disabled: !previewFileAvailable,
                },
                { label: t("knowledgeBases.previewChunks"), value: "chunks" },
              ]}
            />
            {previewNavIndex >= 0 ? (
              <span className={styles.previewModalNav}>
                <Tooltip title={t("knowledgeBases.previewPrev")}>
                  <button
                    type="button"
                    className={styles.previewModalExpandBtn}
                    aria-label={t("knowledgeBases.previewPrev")}
                    disabled={previewNavIndex <= 0}
                    onClick={() => {
                      const prev = previewableDocs[previewNavIndex - 1];
                      if (prev) void openDocumentPreview(prev);
                    }}
                  >
                    <ChevronLeft size={14} strokeWidth={2} aria-hidden />
                  </button>
                </Tooltip>
                <span className={styles.previewModalNavMeta}>
                  {previewNavIndex + 1} / {previewableDocs.length}
                </span>
                <Tooltip title={t("knowledgeBases.previewNext")}>
                  <button
                    type="button"
                    className={styles.previewModalExpandBtn}
                    aria-label={t("knowledgeBases.previewNext")}
                    disabled={previewNavIndex >= previewableDocs.length - 1}
                    onClick={() => {
                      const next = previewableDocs[previewNavIndex + 1];
                      if (next) void openDocumentPreview(next);
                    }}
                  >
                    <ChevronRight size={14} strokeWidth={2} aria-hidden />
                  </button>
                </Tooltip>
              </span>
            ) : null}
            <Tooltip
              title={
                previewMaximized
                  ? t("knowledgeBases.exitFullscreen")
                  : t("knowledgeBases.enterFullscreen")
              }
            >
              <button
                type="button"
                className={styles.previewModalExpandBtn}
                aria-label={
                  previewMaximized
                    ? t("knowledgeBases.exitFullscreen")
                    : t("knowledgeBases.enterFullscreen")
                }
                onClick={togglePreviewFullscreen}
              >
                {previewMaximized ? (
                  <Minimize2 size={14} strokeWidth={2} aria-hidden />
                ) : (
                  <Maximize2 size={14} strokeWidth={2} aria-hidden />
                )}
              </button>
            </Tooltip>
            <Tooltip title={t("common.close", "关闭")}>
              <button
                type="button"
                className={styles.previewModalExpandBtn}
                aria-label={t("common.close", "关闭")}
                onClick={requestClosePreview}
              >
                <X size={14} strokeWidth={2} aria-hidden />
              </button>
            </Tooltip>
          </div>
        }
        open={previewOpen && Boolean(loadedBaseId)}
        onCancel={requestClosePreview}
        closable={false}
        footer={
          previewDocId && previewHasOriginal ? (
            <Button
              icon={<Download size={14} />}
              onClick={downloadPreviewOriginal}
            >
              {t("knowledgeBases.downloadOriginal")}
            </Button>
          ) : null
        }
        width={
          previewMaximized ? "100vw" : isRichPreview ? "min(1200px, 92vw)" : 720
        }
        centered={!previewMaximized}
        destroyOnHidden
        className={previewMaximized ? styles.previewModalMaximized : undefined}
        wrapClassName={styles.previewModalWrap}
        style={
          previewMaximized
            ? { top: 0, maxWidth: "100vw" }
            : // Cap the dialog itself (antd paddings + responsive margins from
              // styles/layout.css stack on top of the content max-height).
              { maxHeight: "calc(100vh - 48px)" }
        }
        classNames={{
          content: previewMaximized
            ? styles.previewModalContentMaximized
            : styles.previewModalContent,
        }}
        styles={{
          body: {
            // Desired height only — the content flex column + max-height
            // (see .previewModalContent) shrinks this when the viewport
            // cannot fit it, so the wrap never grows a scrollbar.
            height: previewMaximized ? undefined : "78vh",
            flex: previewMaximized ? "1 1 auto" : "0 1 auto",
            minHeight: 0,
            padding: 12,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        {previewMode === "chunks" ? (
          <div className={styles.chunkList}>
            {chunkLoading ? (
              <Spin />
            ) : chunkError ? (
              <Alert type="error" showIcon message={chunkError} />
            ) : chunkItems.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={t("knowledgeBases.chunksEmpty")}
              />
            ) : (
              chunkItems.map((chunk) => (
                <article key={chunk.ordinal} className={styles.chunkCard}>
                  <div className={styles.chunkCardLabel}>
                    {t("knowledgeBases.chunkLabel", { n: chunk.ordinal + 1 })}
                  </div>
                  <pre className={styles.chunkCardText}>{chunk.text}</pre>
                </article>
              ))
            )}
          </div>
        ) : previewKind && previewDocId && loadedBaseId ? (
          <div
            className={styles.richPreviewBody}
            data-preview-fullscreen-root=""
          >
            <DocumentPreviewCore
              key={previewDocId}
              kind={previewKind}
              filename={previewFilename}
              fetchBlob={fetchPreviewBlob}
              onDownload={
                previewHasOriginal ? downloadPreviewOriginal : undefined
              }
            />
          </div>
        ) : previewAsMarkdown ? (
          <div className={styles.mdPreviewWrap} data-preview-fullscreen-root="">
            <div className={styles.mdPreviewToolbar}>
              <Tooltip
                title={
                  mdOutlineItems.length === 0
                    ? t("knowledgeBases.mdOutlineEmpty")
                    : t("knowledgeBases.mdOutline")
                }
              >
                <span className={styles.mdOutlineToggleHost}>
                  <button
                    type="button"
                    className={styles.previewModalExpandBtn}
                    aria-label={t("knowledgeBases.mdOutline")}
                    disabled={mdOutlineItems.length === 0}
                    onClick={() => setMdOutlineOpen((v) => !v)}
                  >
                    <ListTree size={14} strokeWidth={2} aria-hidden />
                  </button>
                </span>
              </Tooltip>
              {mdOutlineItems.length > 0 ? (
                <span className={styles.mdPreviewMeta}>
                  {t("knowledgeBases.mdOutlineCount", {
                    count: mdOutlineItems.length,
                  })}
                </span>
              ) : null}
              <span className={styles.mdPreviewToolbarSpacer} />
              {mdFindOpen ? (
                <span className={styles.mdFindBar}>
                  <Input
                    ref={mdFindInputRef}
                    size="small"
                    allowClear
                    value={mdFindQuery}
                    placeholder={t("knowledgeBases.mdFindPlaceholder")}
                    onChange={(event) => {
                      setMdFindQuery(event.target.value);
                      setMdFindIndex(0);
                    }}
                    onPressEnter={(event) => {
                      if (!isImeComposing(event)) jumpMdFind(1);
                    }}
                    onKeyDown={(event) => {
                      if (isImeComposing(event)) return;
                      if (event.key === "Escape") {
                        event.stopPropagation();
                        setMdFindOpen(false);
                        setMdFindQuery("");
                      }
                    }}
                    className={styles.mdFindInput}
                  />
                  <span className={styles.mdPreviewMeta}>
                    {mdFindHitCount === 0
                      ? "0"
                      : `${Math.min(
                          mdFindIndex + 1,
                          mdFindHitCount,
                        )} / ${mdFindHitCount}`}
                  </span>
                  <button
                    type="button"
                    className={styles.previewModalExpandBtn}
                    aria-label={t("knowledgeBases.mdFindPrev")}
                    disabled={mdFindHitCount === 0}
                    onClick={() => jumpMdFind(-1)}
                  >
                    <ChevronLeft size={14} strokeWidth={2} aria-hidden />
                  </button>
                  <button
                    type="button"
                    className={styles.previewModalExpandBtn}
                    aria-label={t("knowledgeBases.mdFindNext")}
                    disabled={mdFindHitCount === 0}
                    onClick={() => jumpMdFind(1)}
                  >
                    <ChevronRight size={14} strokeWidth={2} aria-hidden />
                  </button>
                </span>
              ) : (
                <Tooltip title={t("knowledgeBases.mdFind")}>
                  <button
                    type="button"
                    className={styles.previewModalExpandBtn}
                    aria-label={t("knowledgeBases.mdFind")}
                    onClick={() => {
                      setMdFindOpen(true);
                      window.setTimeout(
                        () => mdFindInputRef.current?.focus(),
                        0,
                      );
                    }}
                  >
                    <Search size={14} strokeWidth={2} aria-hidden />
                  </button>
                </Tooltip>
              )}
            </div>
            <div className={styles.mdPreviewRow}>
              {mdOutlineOpen && mdOutlineItems.length > 0 && !isMobile ? (
                <nav
                  className={styles.mdOutlinePanel}
                  aria-label={t("knowledgeBases.mdOutline")}
                >
                  {mdOutlineItems.map((item, index) => (
                    <button
                      key={index}
                      type="button"
                      className={cx(
                        styles.mdOutlineItem,
                        index === mdActiveOutlineIndex &&
                          styles.mdOutlineItemActive,
                      )}
                      style={{ paddingLeft: 10 + (item.level - 1) * 14 }}
                      title={item.title}
                      onClick={() => scrollToMdHeading(item)}
                    >
                      {item.title}
                    </button>
                  ))}
                </nav>
              ) : null}
              {isMobile ? (
                <Drawer
                  title={t("knowledgeBases.mdOutline")}
                  placement="left"
                  open={mdOutlineOpen && mdOutlineItems.length > 0}
                  onClose={() => setMdOutlineOpen(false)}
                  width={280}
                  destroyOnHidden
                >
                  {mdOutlineItems.map((item, index) => (
                    <button
                      key={index}
                      type="button"
                      className={cx(
                        styles.mdOutlineItem,
                        index === mdActiveOutlineIndex &&
                          styles.mdOutlineItemActive,
                      )}
                      style={{ paddingLeft: 10 + (item.level - 1) * 14 }}
                      title={item.title}
                      onClick={() => {
                        scrollToMdHeading(item);
                        setMdOutlineOpen(false);
                      }}
                    >
                      {item.title}
                    </button>
                  ))}
                </Drawer>
              ) : null}
              <div className={styles.mdPreviewBody} ref={mdBodyRef}>
                {previewLoading ? (
                  <DocumentPreviewLoading phase="file" />
                ) : previewText.trim() &&
                  previewText !== t("knowledgeBases.previewEmpty") ? (
                  <Markdown content={stripFrontmatter(previewText)} />
                ) : (
                  <span className={styles.mdPreviewEmpty}>
                    {t("knowledgeBases.previewEmpty")}
                  </span>
                )}
              </div>
            </div>
          </div>
        ) : previewLoading ? (
          <DocumentPreviewLoading phase="file" />
        ) : (
          <pre className={styles.previewBody}>{previewText}</pre>
        )}
      </Modal>

      <Modal
        title={t("knowledgeBases.createFolder")}
        open={folderModalOpen}
        onCancel={() => setFolderModalOpen(false)}
        onOk={() => void createFolder()}
        okText={t("common.create")}
        cancelText={t("common.cancel")}
        destroyOnHidden
      >
        <Input
          value={folderName}
          onChange={(event) => setFolderName(event.target.value)}
          placeholder={t("knowledgeBases.folderNamePlaceholder")}
          onPressEnter={(event) => {
            if (!isImeComposing(event)) void createFolder();
          }}
        />
      </Modal>

      <Modal
        title={t(
          renameTarget && !renameTarget.is_dir
            ? "knowledgeBases.renameFile"
            : "knowledgeBases.renameFolder",
        )}
        open={renameModalOpen}
        onCancel={() => setRenameModalOpen(false)}
        onOk={() => void renameEntry()}
        okText={t("common.save")}
        cancelText={t("common.cancel")}
        destroyOnHidden
      >
        <Input
          autoFocus
          value={renameName}
          addonAfter={
            renameTarget && !renameTarget.is_dir
              ? knowledgeFileNameParts(
                  renameTarget.path || renameTarget.filename,
                ).suffix || undefined
              : undefined
          }
          onChange={(event) => setRenameName(event.target.value)}
          placeholder={t(
            renameTarget && !renameTarget.is_dir
              ? "knowledgeBases.filenamePlaceholder"
              : "knowledgeBases.folderNamePlaceholder",
          )}
          onPressEnter={(event) => {
            if (!isImeComposing(event)) void renameEntry();
          }}
        />
      </Modal>

      <TextDocumentEditorModal
        open={textEditorOpen}
        mode={textEditorMode}
        loading={textEditorLoading}
        saving={textEditorSaving}
        initialName={textEditorName}
        initialFormat={textEditorFormat}
        initialContent={textEditorContent}
        onCancel={() => setTextEditorOpen(false)}
        onSubmit={saveTextDocument}
      />

      <Drawer
        title={t(
          editingBaseId ? "knowledgeBases.edit" : "knowledgeBases.create",
        )}
        placement="right"
        open={baseDrawerOpen}
        onClose={() => setBaseDrawerOpen(false)}
        width={isMobile ? "100%" : 480}
        destroyOnHidden
        className={styles.baseDrawer}
        footer={
          <div className={styles.drawerFooter}>
            <Button onClick={() => setBaseDrawerOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button type="primary" onClick={() => void saveBase()}>
              {t(editingBaseId ? "common.save" : "common.create")}
            </Button>
          </div>
        }
      >
        <Form
          form={baseForm}
          layout="vertical"
          className={styles.baseForm}
          requiredMark={false}
        >
          <Form.Item name="icon_name" hidden>
            <Input />
          </Form.Item>
          <Form.Item name="icon_url" hidden>
            <Input />
          </Form.Item>
          <Form.Item
            label={t("knowledgeBases.icon")}
            className={avatarPickerStyles.formItem}
          >
            <ResourceAvatarPicker
              imageUrl={
                iconUrl?.trim() &&
                (isUploadedAvatarUrl(iconUrl) ||
                  Boolean(pendingAvatarFile) ||
                  iconUrl.startsWith("blob:") ||
                  /^https?:\/\//i.test(iconUrl))
                  ? iconUrl
                  : null
              }
              presetId={iconName || null}
              presets={KNOWLEDGE_AVATAR_PRESETS}
              disabled={avatarBusy}
              onPick={(file) => {
                if (editingBaseId) {
                  void (async () => {
                    setAvatarBusy(true);
                    try {
                      const result = await knowledgeBasesApi.uploadAvatar(
                        editingBaseId,
                        file,
                      );
                      baseForm.setFieldsValue({ icon_url: result.icon_url });
                      setPendingAvatarFile(null);
                    } catch (err) {
                      message.error(
                        apiErrorMessage(
                          err,
                          t("experts.avatarUploadFailed"),
                          t,
                        ),
                      );
                      throw err;
                    } finally {
                      setAvatarBusy(false);
                    }
                  })();
                  return;
                }
                setPendingAvatarFile(file);
                baseForm.setFieldsValue({
                  icon_url: URL.createObjectURL(file),
                });
              }}
              onSelectPreset={(presetId) => {
                void (async () => {
                  setPendingAvatarFile(null);
                  const nextName = presetId ?? "book-open";
                  if (editingBaseId && isUploadedAvatarUrl(iconUrl)) {
                    setAvatarBusy(true);
                    try {
                      await knowledgeBasesApi.deleteAvatar(editingBaseId);
                    } catch {
                      // Preset still applied locally / on save.
                    } finally {
                      setAvatarBusy(false);
                    }
                  }
                  baseForm.setFieldsValue({
                    icon_name: nextName,
                    icon_url: "",
                  });
                })();
              }}
              onRemove={
                iconUrl || pendingAvatarFile
                  ? () => {
                      void (async () => {
                        setPendingAvatarFile(null);
                        if (editingBaseId && isUploadedAvatarUrl(iconUrl)) {
                          setAvatarBusy(true);
                          try {
                            await knowledgeBasesApi.deleteAvatar(editingBaseId);
                          } catch (err) {
                            message.error(
                              apiErrorMessage(
                                err,
                                t("experts.avatarRemoveFailed"),
                                t,
                              ),
                            );
                          } finally {
                            setAvatarBusy(false);
                          }
                        }
                        baseForm.setFieldsValue({ icon_url: "" });
                      })();
                    }
                  : undefined
              }
            />
          </Form.Item>
          <Form.Item
            name="name"
            label={t("knowledgeBases.name")}
            rules={[
              {
                required: true,
                whitespace: true,
                message: t("knowledgeBases.nameRequired"),
              },
            ]}
          >
            <Input autoFocus maxLength={200} />
          </Form.Item>
          <Form.Item
            name="description"
            label={t("knowledgeBases.description")}
            className={styles.textAssistField}
          >
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} />
          </Form.Item>
          <TextAssistBar
            kind="knowledge_base.description"
            getName={() => String(baseForm.getFieldValue("name") ?? "")}
            getText={() => String(baseForm.getFieldValue("description") ?? "")}
            onApply={(text) =>
              baseForm.setFieldsValue({ description: text.slice(0, 2000) })
            }
          />
          <Form.Item
            name="max_documents"
            label={t("knowledgeBases.maxDocuments")}
            tooltip={t("knowledgeBases.maxDocumentsHint")}
          >
            <InputNumber
              min={0}
              max={10000}
              step={1}
              precision={0}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <div className={styles.formOptions}>
            <div className={styles.formOptionRow}>
              <div className={styles.formOptionCopy}>
                <span className={styles.switchLabel}>
                  {t("knowledgeBases.defaultOpen")}
                </span>
                <span className={styles.formOptionHint}>
                  {t("knowledgeBases.defaultOpenHint")}
                </span>
              </div>
              <Switch
                size="small"
                checked={defaultOpenChecked}
                onChange={setDefaultOpenChecked}
              />
            </div>
            {user?.role === "admin" && (
              <ResourceVisibilityGate allowOwner visibility={baseVisibility}>
                <div className={styles.formOptionRowVisibility}>
                  <div className={styles.formOptionCopy}>
                    <span className={styles.switchLabel}>
                      {t("resourceVisibility.label")}
                    </span>
                  </div>
                  <ResourceVisibilityFields
                    allowOwner
                    visibility={baseVisibility}
                    groupIds={baseGroupIds}
                    onChange={(next) => {
                      setBaseVisibility(next.visibility);
                      setBaseGroupIds(next.groupIds);
                    }}
                  />
                </div>
              </ResourceVisibilityGate>
            )}
          </div>
        </Form>
      </Drawer>

      <Drawer
        title={t("knowledgeBases.settingsTitle")}
        placement="right"
        width={isMobile ? "100%" : 520}
        // Below antd's default dialog layer (1000) so confirm/progress modals
        // opened from inside this drawer always stack above it.
        zIndex={990}
        open={featureModalOpen}
        onClose={() => setFeatureModalOpen(false)}
        destroyOnHidden
        footer={
          <div className={styles.drawerFooter}>
            <Button onClick={() => setFeatureModalOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              type="primary"
              loading={featureSaving}
              disabled={
                featureEnabledDraft
                  ? !featureModel ||
                    (featureBackend === "remote" && !featureProviderId) ||
                    (ocrEnabledDraft &&
                      ocrBackend === "remote" &&
                      (!ocrProviderId || !ocrModel)) ||
                    (featureBackend === "onnx" &&
                      !catalog.find((model) => model.id === featureModel)
                        ?.downloaded) ||
                    featureOptionsLoading ||
                    onnxDownloading
                  : false
              }
              onClick={() => void saveFeature()}
            >
              {t("common.save")}
            </Button>
          </div>
        }
      >
        <div className={styles.formOptions}>
          <div className={styles.formOptionRow}>
            <div className={styles.formOptionCopy}>
              <span className={styles.switchLabel}>
                {t("knowledgeBases.settingsOpen")}
              </span>
              <span className={styles.formOptionHint}>
                {t("knowledgeBases.settingsLead")}
              </span>
            </div>
            <Switch
              size="small"
              checked={featureEnabledDraft}
              onChange={setFeatureEnabledDraft}
            />
          </div>
        </div>

        {featureEnabledDraft ? (
          <Spin spinning={featureOptionsLoading}>
            <div className={styles.settingsBody}>
              <div className={styles.settingsFieldLabel}>
                {t("knowledgeBases.selectModel")}
              </div>
              <Radio.Group
                className={styles.featureBackend}
                value={featureBackend}
                onChange={(event) => {
                  setFeatureBackend(event.target.value);
                  setFeatureModel(undefined);
                }}
              >
                <Radio value="onnx">{t("knowledgeBases.localOnnx")}</Radio>
                <Radio value="remote">
                  {t("knowledgeBases.remoteEmbedding")}
                </Radio>
              </Radio.Group>
              {featureBackend === "remote" ? (
                <div className={styles.featureFields}>
                  <Select
                    value={featureProviderId}
                    onChange={(id) => {
                      setFeatureProviderId(id);
                      setFeatureModel(undefined);
                    }}
                    placeholder={t("knowledgeBases.selectProvider")}
                    options={remoteProviders.map((provider) => ({
                      value: provider.provider_id,
                      label: provider.provider_name,
                    }))}
                    notFoundContent={t("knowledgeBases.noProviders")}
                  />
                  <div className={styles.onnxModelList}>
                    {(
                      remoteProviders.find(
                        (provider) =>
                          provider.provider_id === featureProviderId,
                      )?.models ?? []
                    ).map((model) => {
                      const selected = featureModel === model.id;
                      return (
                        <button
                          key={model.id}
                          type="button"
                          className={cx(
                            styles.onnxModelItem,
                            selected && styles.onnxModelItemActive,
                          )}
                          onClick={() => setFeatureModel(model.id)}
                        >
                          <span className={styles.onnxModelName}>
                            {model.name}
                          </span>
                        </button>
                      );
                    })}
                    {featureProviderId &&
                    (remoteProviders.find(
                      (provider) => provider.provider_id === featureProviderId,
                    )?.models.length ?? 0) === 0 ? (
                      <Typography.Text
                        type="secondary"
                        className={styles.settingsHint}
                      >
                        {t("knowledgeBases.noModels")}
                      </Typography.Text>
                    ) : null}
                  </div>
                </div>
              ) : (
                <div className={styles.onnxModelList}>
                  {catalog.map((model) => {
                    const selected = featureModel === model.id;
                    const size = formatSizeGb(model.size_gb);
                    const downloading =
                      onnxDownloading && downloadProgressModel === model.id;
                    return (
                      <div
                        key={model.id}
                        className={cx(
                          styles.onnxModelItem,
                          selected && styles.onnxModelItemActive,
                        )}
                        onClick={() => setFeatureModel(model.id)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setFeatureModel(model.id);
                          }
                        }}
                        role="button"
                        tabIndex={0}
                      >
                        <div className={styles.onnxModelInfo}>
                          <span className={styles.onnxModelName}>
                            {model.name}
                          </span>
                          <span className={styles.onnxModelMeta}>
                            {model.recommended
                              ? t("knowledgeBases.recommended")
                              : null}
                            {model.recommended ? " · " : null}
                            {size
                              ? t("knowledgeBases.approxSize", { size })
                              : t("knowledgeBases.sizeUnknown")}
                            {model.downloaded
                              ? null
                              : ` · ${t("knowledgeBases.notDownloaded")}`}
                          </span>
                        </div>
                        {model.downloaded ? null : (
                          <Button
                            size="small"
                            icon={<Download size={14} />}
                            loading={downloading}
                            disabled={onnxDownloading && !downloading}
                            onClick={(event) => {
                              event.stopPropagation();
                              setFeatureModel(model.id);
                              void startOnnxDownload(model.id);
                            }}
                          >
                            {t("knowledgeBases.downloadModel")}
                          </Button>
                        )}
                      </div>
                    );
                  })}
                  {catalog.length === 0 ? (
                    <Typography.Text
                      type="secondary"
                      className={styles.settingsHint}
                    >
                      {t("knowledgeBases.noModels")}
                    </Typography.Text>
                  ) : null}
                  {catalog.length > 0 && !onnxExpanded ? (
                    <Button
                      type="link"
                      loading={onnxExpanding}
                      className={styles.showMoreOnnx}
                      onClick={() => void loadEmbeddingOptions(true)}
                    >
                      {t("knowledgeBases.showMoreOnnx")}
                    </Button>
                  ) : null}
                </div>
              )}
              {featureBackend === "onnx" && featureModel ? (
                <div className={styles.onnxReadiness}>
                  <ReadinessRow
                    label={t("knowledgeBases.checkRuntime")}
                    ok={Boolean(capability?.checks.deps_available)}
                    okText={t("knowledgeBases.checkInstalled")}
                    failText={t("knowledgeBases.checkRuntimeMissing")}
                  />
                  <ReadinessRow
                    label={t("knowledgeBases.checkWeights")}
                    ok={Boolean(
                      catalog.find((model) => model.id === featureModel)
                        ?.downloaded,
                    )}
                    okText={t("knowledgeBases.checkDownloaded")}
                    failText={t("knowledgeBases.notDownloaded")}
                  />
                  <div className={styles.onnxReadinessRow}>
                    <span className={styles.onnxReadinessLabel}>
                      {t("knowledgeBases.checkEncode")}
                    </span>
                    {onnxProbe ? (
                      <Typography.Text
                        type={onnxProbe.ok ? "success" : "danger"}
                      >
                        {onnxProbe.ok
                          ? t("knowledgeBases.probeOk", {
                              dim: onnxProbe.dim ?? "?",
                              ms: Math.round(onnxProbe.latency_ms ?? 0),
                            })
                          : onnxProbe.error ?? t("knowledgeBases.probeFailed")}
                      </Typography.Text>
                    ) : (
                      <Typography.Text type="secondary">
                        {t("knowledgeBases.probeIdle")}
                      </Typography.Text>
                    )}
                    <Button
                      type="link"
                      size="small"
                      loading={onnxProbing}
                      onClick={() => void runOnnxProbe()}
                    >
                      {t("knowledgeBases.probeRun")}
                    </Button>
                  </div>
                </div>
              ) : null}
              <Typography.Text type="secondary" className={styles.settingsHint}>
                {t("knowledgeBases.enableDescription")}
              </Typography.Text>
              <Typography.Link onClick={() => navigate("/admin/models")}>
                {t("knowledgeBases.manageModels")}
              </Typography.Link>
              <div className={styles.ocrSettings}>
                <div className={styles.formOptionRow}>
                  <div className={styles.formOptionCopy}>
                    <span className={styles.switchLabel}>
                      {t("knowledgeBases.ocrTitle")}
                    </span>
                    <span className={styles.formOptionHint}>
                      {t("knowledgeBases.ocrDescription")}
                    </span>
                  </div>
                  <Switch
                    size="small"
                    checked={ocrEnabledDraft}
                    onChange={setOcrEnabledDraft}
                  />
                </div>
                {ocrEnabledDraft ? (
                  <div className={styles.featureFields}>
                    <Radio.Group
                      className={styles.featureBackend}
                      value={ocrBackend}
                      onChange={(event) => {
                        const backend = event.target.value as "onnx" | "remote";
                        setOcrBackend(backend);
                        setOcrModel(
                          backend === "onnx" ? "rapidocr" : undefined,
                        );
                        setOcrProviderId(undefined);
                      }}
                    >
                      <Radio value="onnx">{t("knowledgeBases.ocrLocal")}</Radio>
                      <Radio value="remote">
                        {t("knowledgeBases.ocrRemote")}
                      </Radio>
                    </Radio.Group>
                    {ocrBackend === "onnx" ? (
                      <div
                        className={cx(
                          styles.onnxModelItem,
                          styles.onnxModelItemActive,
                        )}
                      >
                        <div className={styles.onnxModelInfo}>
                          <span className={styles.onnxModelName}>
                            RapidOCR (ONNX)
                          </span>
                          <span className={styles.onnxModelMeta}>
                            {t("knowledgeBases.ocrLocalHint")}
                          </span>
                        </div>
                      </div>
                    ) : (
                      <>
                        <Alert
                          type="warning"
                          showIcon
                          message={t("knowledgeBases.ocrRemotePrivacy")}
                        />
                        <Select
                          value={ocrProviderId}
                          onChange={(id) => {
                            setOcrProviderId(id);
                            setOcrModel(undefined);
                          }}
                          placeholder={t("knowledgeBases.selectProvider")}
                          options={ocrRemoteProviders.map((provider) => ({
                            value: provider.provider_id,
                            label: provider.provider_name,
                          }))}
                          notFoundContent={t("knowledgeBases.ocrNoProviders")}
                        />
                        <Select
                          value={ocrModel}
                          onChange={setOcrModel}
                          placeholder={t("knowledgeBases.ocrSelectModel")}
                          options={(
                            ocrRemoteProviders.find(
                              (provider) =>
                                provider.provider_id === ocrProviderId,
                            )?.models ?? []
                          ).map((model) => ({
                            value: model.id,
                            label: model.name,
                          }))}
                          disabled={!ocrProviderId}
                          notFoundContent={t("knowledgeBases.ocrNoModels")}
                        />
                      </>
                    )}
                  </div>
                ) : null}
              </div>
            </div>
          </Spin>
        ) : null}
      </Drawer>
      {pendingImport ? (
        <ImportConfirmation
          grants={importGrants}
          onCancel={() => setPendingImport(null)}
          onConfirm={(grantId) => {
            const pending = pendingImport;
            setPendingImport(null);
            if (
              !deviceId ||
              selected?.id !== pending.baseId ||
              selected.shared ||
              currentFolder !== pending.folder
            )
              return;
            try {
              const requestId = Array.from(
                crypto.getRandomValues(new Uint8Array(16)),
                (byte) => byte.toString(16).padStart(2, "0"),
              ).join("");
              activeImport.current = {
                baseId: pending.baseId,
                deviceId,
                requestId,
              };
              setImportBusy(true);
              sendNativeFileCommand({
                action: "knowledge_import",
                request_id: requestId,
                source_grant: grantId,
                knowledge_base_id: pending.baseId,
                knowledge_folder: pending.folder,
              });
            } catch {
              activeImport.current = null;
              setImportBusy(false);
              message.error(t("knowledgeBases.importDeviceRequired"));
            }
          }}
        />
      ) : null}
      <Modal
        open={downloadProgressOpen}
        title={t("models.localDownloadProgressTitle")}
        onCancel={dismissDownloadProgressToBackground}
        closable
        maskClosable
        destroyOnHidden={false}
        // Above modal.confirm (zIndexPopupBase + CONTAINER_MAX_OFFSET = 2000)
        // so this dialog is not covered while the confirm is closing.
        zIndex={2100}
        footer={
          <Button onClick={dismissDownloadProgressToBackground}>
            {t("models.localDownloadContinueBackground")}
          </Button>
        }
      >
        <div className={styles.downloadProgressLabel}>
          {downloadProgressLabel || downloadProgressModel}
        </div>
        <Progress percent={downloadProgress} status="active" />
        <Typography.Text type="secondary" className={styles.settingsHint}>
          {t("models.localDownloadBackgroundHint")}
        </Typography.Text>
      </Modal>
    </PageShell>
  );
}
