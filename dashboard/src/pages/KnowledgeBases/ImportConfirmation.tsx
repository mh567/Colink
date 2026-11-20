import { useId, useState } from "react";
import { Modal } from "antd";
import { useTranslation } from "react-i18next";

export default function ImportConfirmation({
  grants,
  onConfirm,
  onCancel,
}: {
  grants: { id: string; path: string }[];
  onConfirm: (grantId: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [grant, setGrant] = useState("");
  return (
    <Modal
      open
      title={t("knowledgeBases.importTitle")}
      okText={t("knowledgeBases.importConfirm")}
      cancelText={t("common.cancel")}
      okButtonProps={{ disabled: !grant }}
      onOk={() => onConfirm(grant)}
      onCancel={onCancel}
    >
      <p>{t("knowledgeBases.importScope")}</p>
      <label htmlFor={`${id}-directory`}>
        {t("knowledgeBases.importDirectory")}
      </label>
      <select
        id={`${id}-directory`}
        value={grant}
        onChange={(event) => setGrant(event.target.value)}
        style={{ width: "100%", marginBottom: 12 }}
      >
        <option value="">{t("knowledgeBases.importSelectDirectory")}</option>
        {grants.map((item) => (
          <option key={item.id} value={item.id}>
            {item.path}
          </option>
        ))}
      </select>
    </Modal>
  );
}
