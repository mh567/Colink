import { parseKnowledgeCitations } from "./parseKnowledgeCitations";
import type { KnowledgeCitation } from "./parseKnowledgeCitations";
import type { ChatMessage } from "../pages/Chat/hooks/useChat";

export type { KnowledgeCitation };

/** Collect unique knowledge citations from tool results in an assistant turn. */
export function collectTurnKnowledgeCitations(
  messages: readonly ChatMessage[],
): KnowledgeCitation[] {
  const out: KnowledgeCitation[] = [];
  const seen = new Set<string>();
  for (const msg of messages) {
    if (msg.toolData?.name !== "search_knowledge") continue;
    const { citations } = parseKnowledgeCitations(msg.toolData.output);
    for (const citation of citations) {
      if (seen.has(citation.docId)) continue;
      seen.add(citation.docId);
      out.push(citation);
    }
  }
  return out;
}
