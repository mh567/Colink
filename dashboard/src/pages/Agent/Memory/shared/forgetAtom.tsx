/**
 * forgetAtom — shared confirmation flow for permanently forgetting one memory.
 *
 * Performs physical deletion of the memory fact, its associated raw events,
 * and internal search indexes, while leaving audit logs intact.
 */
import { Typography } from "antd";
import { message } from "@/utils/antdMessage";
import { modal } from "@/utils/antdModal";
import i18n from "@/i18n";

import {
  memoryDashboardApi,
  type AtomItem,
} from "../../../../api/modules/memoryDashboard";

export function confirmForgetAtom({
  agentId,
  atom,
  onSuccess,
}: {
  agentId: string;
  atom: AtomItem;
  /** Callback after successful forgetting, usually to close drawer and refresh list. */
  onSuccess?: () => void;
}) {
  modal.confirm({
    title: i18n.t("memory.forget.title", "彻底遗忘这条记忆？"),
    content: (
      <div>
        <Typography.Paragraph
          style={{ fontSize: 13, color: "#1f1f1f", marginBottom: 8 }}
        >
          {atom.assertion}
        </Typography.Paragraph>
        <Typography.Paragraph
          type="secondary"
          style={{ fontSize: 12, marginBottom: 0 }}
        >
          {i18n.t(
            "memory.forget.description",
            "该操作将从数据库中物理删除该记忆事实，并清除关联事件、索引与内部记录。已清除的记忆无法恢复，且未来即便还原旧备份也将保持遗忘。注意：此操作不删除法定90天安全审计日志，亦不影响会话记录。",
          )}
        </Typography.Paragraph>
      </div>
    ),
    okText: i18n.t("memory.forget.ok", "确认彻底遗忘"),
    okType: "danger",
    cancelText: i18n.t("common.cancel", "取消"),
    onOk: async () => {
      try {
        await memoryDashboardApi.forgetAtom(agentId, atom.id);
        message.success(
          i18n.t("memory.forget.success", "已成功彻底遗忘该记忆"),
        );
        onSuccess?.();
      } catch (e) {
        message.error(
          i18n.t("memory.forget.failed", {
            message: (e as Error).message ?? e,
            defaultValue: `遗忘失败：${(e as Error).message ?? e}`,
          }),
        );
        throw e; // Keep the confirmation dialog open.
      }
    },
  });
}
