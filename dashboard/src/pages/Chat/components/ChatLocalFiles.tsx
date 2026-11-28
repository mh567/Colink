import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { LocalFileScope } from "../../../api/desktop";
import styles from "../index.module.less";

export default function ChatLocalFiles({
  scopes,
  busy,
  error,
  onRemove,
  onCancel,
}: {
  scopes: LocalFileScope[];
  busy: boolean;
  error: string | null;
  onRemove: (id: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  if (!scopes.length && !busy && !error) return null;
  return (
    <section
      className={styles.localFiles}
      aria-label={t("localFiles.action", "Use on this computer")}
    >
      {scopes.length > 0 && (
        <>
          <strong>
            {t("localFiles.scopeTitle", "Local material for this message")}
          </strong>
          <ul>
            {scopes.map((scope) => (
              <li key={scope.id}>
                <span>{scope.path}</span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onRemove(scope.id)}
                  aria-label={t("localFiles.remove", "Remove {{name}}", {
                    name: scope.name,
                  })}
                >
                  <X size={14} />
                </button>
              </li>
            ))}
          </ul>
          <p>
            {t(
              "localFiles.scopeHint",
              "Only this message can read the selected TXT, MD and CSV files. Necessary text goes to the enterprise model; originals stay here. Removing a selection cannot retract content already sent to the conversation.",
            )}
          </p>
        </>
      )}
      {busy && (
        <button type="button" onClick={onCancel}>
          {t("localFiles.cancel", "Cancel local reading")}
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
