import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { knowledgeBasesApi } from "../../../api/modules/knowledgeBases";
import type { KnowledgeCitationRef } from "../../../api/modules/knowledgeBases";
import type { KnowledgeCitation } from "../../../utils/parseKnowledgeCitations";

const CitationStatusContext = createContext<ReadonlySet<string>>(new Set());
const citationKey = (kbId: string, docId: string) =>
  JSON.stringify([kbId, docId]);
const isValidStatusId = (id: string) => id.length > 0 && [...id].length <= 64;

export function useWithdrawnCitations() {
  const withdrawn = useContext(CitationStatusContext);
  return (citation: KnowledgeCitation) =>
    withdrawn.has(citationKey(citation.kbId, citation.docId));
}

export function KnowledgeCitationStatusProvider({
  refsKey,
  children,
}: {
  refsKey: string;
  children: ReactNode;
}) {
  const [withdrawn, setWithdrawn] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    const historyRefs: KnowledgeCitationRef[] = JSON.parse(refsKey);
    // Legacy citations can still render even if the status API would reject them.
    const refs = historyRefs.filter(
      ({ kb_id, doc_id }) => isValidStatusId(kb_id) && isValidStatusId(doc_id),
    );
    if (!refs.length) {
      setWithdrawn(new Set());
      return;
    }
    let pending: AbortController | undefined;
    const refresh = () => {
      if (document.visibilityState === "hidden") return;
      pending?.abort();
      const request = (pending = new AbortController());
      const batches = [];
      for (let start = 0; start < refs.length; start += 200) {
        batches.push(
          knowledgeBasesApi.citationStatus(
            refs.slice(start, start + 200),
            request.signal,
          ),
        );
      }
      void Promise.all(batches).then(
        (results) => {
          if (!request.signal.aborted) {
            setWithdrawn(
              new Set(
                results.flatMap((batch) =>
                  batch.withdrawn.map((ref) =>
                    citationKey(ref.kb_id, ref.doc_id),
                  ),
                ),
              ),
            );
          }
        },
        () => {
          // A transient request failure does not change the last known status.
        },
      );
    };
    refresh();
    window.addEventListener("focus", refresh);
    const interval = window.setInterval(refresh, 30_000);
    return () => {
      pending?.abort();
      window.removeEventListener("focus", refresh);
      window.clearInterval(interval);
    };
  }, [refsKey]);

  return (
    <CitationStatusContext.Provider value={withdrawn}>
      {children}
    </CitationStatusContext.Provider>
  );
}
