import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Divider,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  devicesApi,
  type DeviceStatus,
  type DeviceView,
} from "../../api/modules/devices";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useDesktopDevice } from "../../context/DesktopDeviceContext";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import PageShell from "../../layouts/PageShell";
import { apiErrorMessage } from "../../utils/apiError";
import { formatServerDateTime } from "../../utils/formatMessageTime";

const STATUS_COLORS: Record<DeviceStatus, string> = {
  pending: "gold",
  approved: "green",
  revoked: "default",
};

export default function DevicesPage() {
  const { t } = useTranslation();
  const user = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const timeZone = useServerTimezone();
  const native = useDesktopDevice();
  const [devices, setDevices] = useState<DeviceView[]>([]);
  const [managed, setManaged] = useState<DeviceView[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [manageError, setManageError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const requestId = useRef(0);

  const refresh = useCallback(async () => {
    const current = ++requestId.current;
    setLoading(true);
    const [ownResult, manageResult] = await Promise.allSettled([
      devicesApi.list(),
      isAdmin ? devicesApi.manage() : Promise.resolve([] as DeviceView[]),
    ]);
    if (current !== requestId.current) return;
    if (ownResult.status === "fulfilled") {
      setDevices(ownResult.value);
      setLoadError(null);
    } else setLoadError(ownResult.reason);
    if (manageResult.status === "fulfilled") {
      setManaged(manageResult.value);
      setManageError(null);
    } else setManageError(manageResult.reason);
    setLoading(false);
  }, [isAdmin]);

  useEffect(() => {
    setDevices([]);
    setManaged([]);
    setLoadError(null);
    setManageError(null);
    setActionError(null);
    void refresh();
    return () => {
      requestId.current += 1;
    };
  }, [refresh, user?.id]);

  useEffect(() => {
    if (native.event) void refresh();
  }, [native.event, refresh]);

  const mutate = async (row: DeviceView, action: "approve" | "revoke") => {
    setWorkingId(row.device_id);
    setActionError(null);
    try {
      await devicesApi[action](row.device_id);
      await refresh();
    } catch (error) {
      setActionError(error);
    } finally {
      setWorkingId(null);
    }
  };

  const statusLabel = (status: DeviceStatus) =>
    t(
      `devices.status.${status}`,
      {
        pending: "待批准",
        approved: "已批准",
        revoked: "已撤销",
      }[status],
    );
  const columns: ColumnsType<DeviceView> = [
    { title: t("devices.name", "设备名称"), dataIndex: "name", width: 160 },
    {
      title: t("devices.id", "设备编号"),
      dataIndex: "device_id",
      width: 210,
      render: (value: string) => (
        <Typography.Text code>{value}</Typography.Text>
      ),
    },
    { title: t("devices.platform", "系统"), dataIndex: "platform", width: 90 },
    {
      title: t("devices.approval", "审批状态"),
      dataIndex: "status",
      width: 100,
      render: (status: DeviceStatus) => (
        <Tag color={STATUS_COLORS[status]}>{statusLabel(status)}</Tag>
      ),
    },
    {
      title: t("devices.connection", "连接状态"),
      dataIndex: "online",
      width: 90,
      render: (online: boolean) => (
        <Tag color={online ? "green" : undefined}>
          {online ? t("devices.online", "在线") : t("devices.offline", "离线")}
        </Tag>
      ),
    },
    {
      title: t("devices.lastSeen", "最近接入"),
      dataIndex: "last_seen",
      width: 190,
      render: (value: number | null) =>
        value === null
          ? t("devices.neverSeen", "尚无记录")
          : formatServerDateTime(value, timeZone),
    },
    {
      title: t("devices.createdAt", "登记时间"),
      dataIndex: "created_at",
      width: 190,
      render: (value: number) => formatServerDateTime(value, timeZone),
    },
  ];
  const managementColumns: ColumnsType<DeviceView> = [
    {
      title: t("devices.owner", "所属账号"),
      dataIndex: "owner_username",
      width: 120,
    },
    ...columns,
    {
      title: t("common.actions", "操作"),
      width: 140,
      render: (_, row) => (
        <Space>
          {row.status !== "approved" && (
            <Popconfirm
              title={t("devices.approveTitle", "批准设备「{{name}}」？", {
                name: row.name,
              })}
              description={t(
                "devices.approveConfirm",
                "所属账号：{{owner}}；设备编号：{{id}}。请与员工端核对一致，批准后登记为该员工的可信设备。",
                { owner: row.owner_username, id: row.device_id },
              )}
              okText={t("devices.confirmApprove", "确认批准")}
              cancelText={t("common.cancel", "取消")}
              onConfirm={() => mutate(row, "approve")}
            >
              <Button
                size="small"
                aria-label={t("devices.approve", "批准")}
                disabled={workingId !== null}
              >
                {t("devices.approve", "批准")}
              </Button>
            </Popconfirm>
          )}
          {row.status !== "revoked" && (
            <Popconfirm
              title={t("devices.revokeTitle", "撤销设备「{{name}}」？", {
                name: row.name,
              })}
              description={t(
                "devices.revokeConfirm",
                "所属账号：{{owner}}；设备编号：{{id}}。撤销后，该设备将失去接入授权。",
                { owner: row.owner_username, id: row.device_id },
              )}
              okText={t("devices.confirmRevoke", "确认撤销")}
              cancelText={t("common.cancel", "取消")}
              onConfirm={() => mutate(row, "revoke")}
              okButtonProps={{ danger: true }}
            >
              <Button
                size="small"
                danger
                aria-label={t("devices.revoke", "撤销")}
                disabled={workingId !== null}
              >
                {t("devices.revoke", "撤销")}
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <PageShell
      title={t("devices.title", "我的设备")}
      subtitle={t("devices.subtitle", "查看本人设备的审批和在线状态。")}
      actions={
        <Button
          icon={<RefreshCw size={14} />}
          loading={loading}
          onClick={() => void refresh()}
        >
          {t("common.refresh", "刷新")}
        </Button>
      }
    >
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {native.available ? (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              {t(
                "devices.connectHint",
                "将当前电脑接入本人账号，首次接入需管理员核对设备编号并批准。",
              )}
            </Typography.Text>
            <Space wrap>
              <Button
                type="primary"
                loading={native.busy === "connect"}
                disabled={native.busy !== null}
                onClick={native.connect}
              >
                {t("devices.connect", "接入当前设备")}
              </Button>
              <Button
                loading={native.busy === "disconnect"}
                onClick={native.disconnect}
              >
                {t("devices.disconnect", "断开当前设备")}
              </Button>
            </Space>
            {native.busy !== null && (
              <Typography.Text type="secondary">
                {t("devices.waitingNative", "正在等待桌面客户端回执…")}
              </Typography.Text>
            )}
            {native.event?.type === "status" && (
              <>
                <Typography.Text>
                  {t("devices.currentId", "当前设备编号：{{id}}", {
                    id: native.event.device_id,
                  })}
                </Typography.Text>
                <Space>
                  <Tag color={STATUS_COLORS[native.event.status]}>
                    {statusLabel(native.event.status)}
                  </Tag>
                  <Tag color={native.event.online ? "green" : undefined}>
                    {native.event.online
                      ? t("devices.online", "在线")
                      : t("devices.offline", "离线")}
                  </Tag>
                </Space>
              </>
            )}
            {native.event?.type === "error" && (
              <Alert
                type="error"
                showIcon
                message={`${native.event.code}: ${native.event.message}`}
              />
            )}
            {native.event?.type === "disconnected" && (
              <Alert
                type="info"
                showIcon
                message={t("devices.disconnected", "当前设备已断开")}
              />
            )}
          </Space>
        ) : (
          <Alert
            type="info"
            showIcon
            message={t(
              "devices.desktopRequired",
              "请使用桌面客户端接入当前设备。浏览器仅可查看设备状态。首次接入后需管理员批准。",
            )}
          />
        )}
        {loadError != null && (
          <Alert
            type="error"
            showIcon
            message={apiErrorMessage(
              loadError,
              t("devices.loadFailed", "加载设备失败"),
              t,
            )}
          />
        )}
        <Table<DeviceView>
          rowKey="device_id"
          columns={columns}
          dataSource={devices}
          loading={loading}
          scroll={{ x: 1030 }}
          locale={{ emptyText: t("devices.empty", "尚未登记设备") }}
          pagination={false}
        />
        {isAdmin && (
          <>
            <Divider style={{ margin: "8px 0" }} />
            <div>
              <Typography.Title level={5} style={{ margin: "0 0 8px" }}>
                {t("devices.manageTitle", "设备审批")}
              </Typography.Title>
              <Typography.Text type="secondary">
                {t(
                  "devices.manageHint",
                  "批准前，请与员工核对设备编号。批准后，该设备登记为该员工的可信设备；管理员仅管理授权。",
                )}
              </Typography.Text>
            </div>
            {manageError != null && (
              <Alert
                type="error"
                showIcon
                message={apiErrorMessage(
                  manageError,
                  t("devices.loadFailed", "加载设备失败"),
                  t,
                )}
              />
            )}
            {actionError != null && (
              <Alert
                type="error"
                showIcon
                message={apiErrorMessage(
                  actionError,
                  t("devices.actionFailed", "设备操作失败"),
                  t,
                )}
              />
            )}
            <Table<DeviceView>
              rowKey="device_id"
              columns={managementColumns}
              dataSource={managed}
              loading={loading}
              scroll={{ x: 1290 }}
              locale={{
                emptyText: t("devices.manageEmpty", "暂无待管理的设备"),
              }}
              pagination={false}
            />
          </>
        )}
      </Space>
    </PageShell>
  );
}
