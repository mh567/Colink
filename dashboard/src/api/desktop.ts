export interface LocalFileScope {
  id: string;
  name: string;
  path: string;
  kind: "file" | "directory";
}

export interface LocalFileIdentity {
  accessToken: string;
  agentId: string;
  conversationId: string;
  locale: "zh" | "en";
}

export interface DesktopLocalFiles {
  select(
    request: LocalFileIdentity & { kind: "files" | "directory" },
  ): Promise<LocalFileScope[]>;
  remove(id: string): Promise<void>;
  clear(): Promise<void>;
  consume(request: LocalFileIdentity & { ids: string[] }): Promise<{
    text: string;
    sources: Array<{ name: string; path: string }>;
  }>;
}

declare global {
  interface Window {
    colinkDesktop?: { localFiles: DesktopLocalFiles };
  }
}

export function desktopLocalFiles(): DesktopLocalFiles | undefined {
  return window.colinkDesktop?.localFiles;
}
