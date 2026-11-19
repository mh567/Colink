import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Tooltip } from "antd";
import { FileText } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { KnowledgeCitation } from "../../../utils/parseKnowledgeCitations";
import { knowledgeBasesApi } from "../../../api/modules/knowledgeBases";
import { isNotFoundApiError } from "../../../utils/apiError";
import {
  knowledgeCitationHasNestedPath,
  knowledgeCitationTooltip,
} from "../../../utils/knowledgeCitationDisplay";
import { useChatFilePreview } from "../ChatFilePreviewContext";
import panelStyles from "./KnowledgeCitationPanelContent.module.less";
import styles from "../index.module.less";

function isOverflowing(el: HTMLElement | null): boolean {
  if (!el) return false;
  return el.scrollWidth - el.clientWidth > 1;
}

function KnowledgeCitationChip({
  citation,
  onOpen,
  withdrawn,
}: {
  citation: KnowledgeCitation;
  onOpen: () => void;
  withdrawn: boolean;
}) {
  const { t } = useTranslation();
  const nameRef = useRef<HTMLSpanElement>(null);
  const kbRef = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);

  useLayoutEffect(() => {
    const measure = () => {
      setTruncated(
        isOverflowing(nameRef.current) || isOverflowing(kbRef.current),
      );
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [citation.filename, citation.kbName, citation.path]);

  const showTooltip = knowledgeCitationHasNestedPath(citation) || truncated;
  const tip = knowledgeCitationTooltip(citation);

  // Span wrapper keeps Tooltip + keyboard focus on the real <button>.
  const button = (
    <span className={panelStyles.chipHit}>
      <button
        type="button"
        className={styles.knowledgeCitationChip}
        onClick={onOpen}
        disabled={withdrawn}
      >
        <FileText size={13} strokeWidth={2} aria-hidden />
        <span ref={nameRef} className={styles.knowledgeCitationName}>
          {citation.filename}
        </span>
        {citation.kbName ? (
          <span ref={kbRef} className={styles.knowledgeCitationKb}>
            {citation.kbName}
          </span>
        ) : null}
        {withdrawn ? <span>{t("chat.citationWithdrawn")}</span> : null}
      </button>
    </span>
  );

  if (!showTooltip) return button;

  return (
    <Tooltip title={tip} mouseEnterDelay={0.35}>
      {button}
    </Tooltip>
  );
}

export function KnowledgeCitationsStrip({
  citations,
}: {
  citations: KnowledgeCitation[];
}) {
  const { t } = useTranslation();
  const filePreview = useChatFilePreview();
  const [documents, setDocuments] = useState<Record<string, string[] | null>>(
    {},
  );
  const baseIds = JSON.stringify([
    ...new Set(citations.map((citation) => citation.kbId).filter(Boolean)),
  ]);

  useEffect(() => {
    let active = true;
    const ids: string[] = JSON.parse(baseIds);
    setDocuments({});
    for (const id of ids) {
      void knowledgeBasesApi.listDocuments(id).then(
        (rows) => {
          if (active)
            setDocuments((current) => ({
              ...current,
              [id]: rows
                .filter((row) => row.status !== "withdrawn")
                .map((row) => row.id),
            }));
        },
        (error: unknown) => {
          if (active && isNotFoundApiError(error)) {
            setDocuments((current) => ({ ...current, [id]: null }));
          }
        },
      );
    }
    return () => {
      active = false;
    };
  }, [baseIds]);

  if (citations.length === 0) return null;

  return (
    <div className={styles.knowledgeCitations} aria-label={t("chat.citations")}>
      <div className={styles.knowledgeCitationsLabel}>
        {t("chat.citations")}
      </div>
      <div className={styles.knowledgeCitationsList}>
        {citations.map((citation) => (
          <KnowledgeCitationChip
            key={citation.docId}
            citation={citation}
            withdrawn={
              citation.kbId in documents &&
              (documents[citation.kbId] === null ||
                !documents[citation.kbId]?.includes(citation.docId))
            }
            onOpen={() => filePreview?.openKnowledgeCitation(citation)}
          />
        ))}
      </div>
    </div>
  );
}
