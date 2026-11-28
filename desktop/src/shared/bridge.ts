export interface LocalScope {
  id: string;
  name: string;
  path: string;
  kind: "file" | "directory";
}

export interface ScopeSession {
  accessToken: string;
  agentId: string;
  conversationId: string;
  locale?: "zh" | "en";
}

export interface ScopeSelection extends ScopeSession {
  kind: "files" | "directory";
}

export interface ScopeConsumption extends ScopeSession {
  ids: string[];
}

export interface LocalContext {
  text: string;
  sources: Array<{ name: string; path: string }>;
}

export const CHANNELS = {
  select: "colink:local-select",
  remove: "colink:local-remove",
  clear: "colink:local-clear",
  consume: "colink:local-consume",
  connect: "colink:connect",
  setup: "colink:setup",
} as const;
