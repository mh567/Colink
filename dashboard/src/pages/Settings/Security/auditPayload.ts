const SENSITIVE_KEYS = new Set([
  "password",
  "passwd",
  "api_key",
  "apikey",
  "secret",
  "token",
  "access_token",
  "session_token",
  "content",
  "text",
  "instruction",
  "source_text",
  "file",
  "body",
  "prompt",
]);

export function formatAuditPayload(
  raw: string | null | undefined,
): string | null {
  if (!raw) return null;
  try {
    const data: unknown = JSON.parse(raw);
    if (!data || typeof data !== "object" || Array.isArray(data)) return null;
    const cleaned = Object.fromEntries(
      Object.entries(data as Record<string, unknown>).filter(([key]) => {
        const normalized = key.toLowerCase().replaceAll("_", "");
        return (
          !SENSITIVE_KEYS.has(key.toLowerCase()) &&
          ![...SENSITIVE_KEYS].some(
            (item) => item.replaceAll("_", "") === normalized,
          )
        );
      }),
    );
    return Object.keys(cleaned).length > 0 ? JSON.stringify(cleaned) : null;
  } catch {
    return raw.length <= 200 ? raw : null;
  }
}
