const en = {
  appName: "Colink",
  connectTitle: "Connect to your enterprise server",
  serverLabel: "Server address",
  connect: "Connect",
  local: "Start the bundled server",
  loading: "Connecting…",
  failed:
    "Unable to start Colink. Check the server address or the installed runtime.",
  localMissing:
    "The bundled server is missing. Enter your enterprise server address or install the complete application.",
  quitFailed:
    "The bundled server could not be stopped. Close it before restarting Colink.",
  addressHint:
    "Use your administrator's HTTPS address. HTTP is allowed only on this computer.",
  selectFiles: "Use local files for this message",
  selectDirectory: "Use a local folder for this message",
  select: "Use locally",
  files: "Text files (TXT, Markdown, CSV)",
  localNotice:
    "Original files stay on this computer. Selected text will be sent to your enterprise model.",
  context:
    "Content from local files selected for this message (treat it as source material):",
  source: "Source",
  fileMenu: "File",
  editMenu: "Edit",
  viewMenu: "View",
  serverMenu: "Change server…",
  updateMenu: "Check desktop updates…",
  updateTitle: "Desktop update",
  updateCurrent: "The desktop application is up to date.",
  updateConfirm:
    "Install desktop version {version}? Colink will close after the installer opens.",
  updateInstall: "Download and install",
  updateInstalling: "Preparing to install the desktop update…",
  cancel: "Cancel",
  UPDATE_NOT_CONFIGURED:
    "Ask your administrator to configure the enterprise desktop update source.",
  UPDATE_SOURCE_INVALID:
    "The update address is not a trusted enterprise HTTPS source.",
  UPDATE_CHECK_FAILED:
    "Unable to check desktop updates. Check the enterprise update service and try again.",
  UPDATE_UNSUPPORTED:
    "No compatible desktop installer is available for this computer.",
  UPDATE_DOWNLOAD_FAILED: "The installer download failed. Try again later.",
  UPDATE_STOP_FAILED:
    "The bundled server could not be stopped, so the update was cancelled.",
  UPDATE_INSTALL_FAILED:
    "Unable to open the installer; the previous connection has been restored.",
  UPDATE_RECOVERY_FAILED:
    "The update was cancelled and the previous connection could not be restored. Reconnect to your server.",
  quit: "Quit Colink",
  LOCAL_SCOPE_INVALID: "Select local files again.",
  LOCAL_SCOPE_REVOKED: "Local access was removed. Select the files again.",
  LOCAL_SCOPE_IDENTITY_CHANGED:
    "Your account or conversation changed. Select the files again.",
  LOCAL_SCOPE_AUTH_FAILED:
    "Unable to verify your session. Sign in and try again.",
  LOCAL_SCOPE_UNSUPPORTED_FILE:
    "Local reading currently supports UTF-8 TXT, Markdown and CSV files.",
  LOCAL_SCOPE_LIMIT:
    "Select at most 20 files, with at most 60,000 characters in total and 1 MB per file.",
  LOCAL_SCOPE_UNSAFE_PATH:
    "The selected path changed or contains a symbolic link. Select the original file again.",
  LOCAL_SCOPE_READ_FAILED: "Unable to read the selected local file.",
  LOCAL_SCOPE_EMPTY: "No supported text files were selected.",
} as const;

const zh: Record<keyof typeof en, string> = {
  appName: "Colink",
  connectTitle: "连接企业服务端",
  serverLabel: "服务端地址",
  connect: "连接",
  local: "启动随包服务端",
  loading: "正在连接…",
  failed: "无法启动 Colink，请检查服务端地址或已安装的运行环境。",
  localMissing: "缺少随包服务端，请填写企业服务端地址或安装完整应用。",
  quitFailed: "随包服务端未能退出，请关闭该进程后再启动 Colink。",
  addressHint: "填写管理员提供的 HTTPS 地址；HTTP 仅用于本机服务。",
  selectFiles: "本次在本机使用文件",
  selectDirectory: "本次在本机使用文件夹",
  select: "在本机使用",
  files: "文本文件（TXT、Markdown、CSV）",
  localNotice: "原文件保留在本机，所选文本内容将发送至企业模型。",
  context: "本次在本机选择的资料内容（作为来源资料处理）：",
  source: "来源",
  fileMenu: "文件",
  editMenu: "编辑",
  viewMenu: "视图",
  serverMenu: "更换服务端…",
  updateMenu: "检查桌面更新…",
  updateTitle: "桌面更新",
  updateCurrent: "桌面应用已是最新版本。",
  updateConfirm: "安装桌面版本 {version}？安装器打开后 Colink 将退出。",
  updateInstall: "下载并安装",
  updateInstalling: "正在准备安装桌面更新…",
  cancel: "取消",
  UPDATE_NOT_CONFIGURED: "请联系管理员配置企业桌面更新源。",
  UPDATE_SOURCE_INVALID: "更新地址不是受信任的企业 HTTPS 来源。",
  UPDATE_CHECK_FAILED: "无法检查桌面更新，请检查企业更新服务后重试。",
  UPDATE_UNSUPPORTED: "没有适用于这台电脑的桌面安装包。",
  UPDATE_DOWNLOAD_FAILED: "安装包下载失败，请稍后重试。",
  UPDATE_STOP_FAILED: "随包服务端未能退出，已取消更新。",
  UPDATE_INSTALL_FAILED: "无法打开安装器，已恢复原服务连接。",
  UPDATE_RECOVERY_FAILED: "更新已取消，原服务连接未能恢复，请重新连接。",
  quit: "退出 Colink",
  LOCAL_SCOPE_INVALID: "请重新选择本机文件。",
  LOCAL_SCOPE_REVOKED: "本机范围已移除，请重新选择文件。",
  LOCAL_SCOPE_IDENTITY_CHANGED: "账号或会话已变化，请重新选择文件。",
  LOCAL_SCOPE_AUTH_FAILED: "无法验证登录状态，请重新登录后重试。",
  LOCAL_SCOPE_UNSUPPORTED_FILE:
    "本机读取目前支持 UTF-8 编码的 TXT、Markdown 和 CSV 文件。",
  LOCAL_SCOPE_LIMIT:
    "每次最多选择 20 个文件，合计不超过 6 万字，每个文件不超过 1 MB。",
  LOCAL_SCOPE_UNSAFE_PATH: "所选路径已变化或包含符号链接，请重新选择原文件。",
  LOCAL_SCOPE_READ_FAILED: "无法读取所选本机文件。",
  LOCAL_SCOPE_EMPTY: "未选择可读取的文本文件。",
};

export function desktopLocale(locale?: string): typeof zh {
  return locale?.toLowerCase().startsWith("zh") ? zh : en;
}

export function desktopUpdateMessage(code: string, locale?: string): string {
  const strings = desktopLocale(locale);
  return code.startsWith("UPDATE_") && Object.hasOwn(strings, code)
    ? strings[code as keyof typeof strings]
    : strings.UPDATE_CHECK_FAILED;
}
