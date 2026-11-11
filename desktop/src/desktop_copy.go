package main

import "fmt"

const (
	copyStatusConnecting       = "status.connecting"
	copyStatusCheckingRuntime  = "status.checking_runtime"
	copyStatusStartingService  = "status.starting_service"
	copyStatusReady            = "status.ready"
	copyStatusUsingRuntime     = "status.using_runtime"
	copyStatusBackupDatabase   = "status.backup_database"
	copyStatusInstallingUpdate = "status.installing_update"
	copyStatusUpdatingRuntime  = "status.updating_runtime"
	copyStatusFirstExtract     = "status.first_extract"
	copyStatusUpdateFailedKeep = "status.update_failed_keep"
	copyStatusUsingExisting    = "status.using_existing"
	copyErrorBackupFailed      = "error.backup_failed"
	copyErrorUpgradeFailed     = "error.upgrade_failed"
	copyErrorInvalidRuntime    = "error.invalid_runtime"
	copyErrorNoFreePort        = "error.no_free_port"
	copyErrorCannotBind        = "error.cannot_bind"
	copyWait1Minute            = "wait.1_minute"
	copyWaitNMinutes           = "wait.n_minutes"
	copyWait1Second            = "wait.1_second"
	copyWaitNSeconds           = "wait.n_seconds"
	copyHealthNotReady5xx      = "health.not_ready_5xx"
	copyHealthNotReadyConnect  = "health.not_ready_connect"
	copyHealthNotReady         = "health.not_ready"
	copyTraySettings           = "tray.settings"
	copyTrayShowMain           = "tray.show_main"
	copyTrayQuit               = "tray.quit"
)

const (
	copyFileConfirmTitle            = "file.confirm_title"
	copyFileConfirmMessage          = "file.confirm_message"
	copyFileDelete                  = "file.delete"
	copyFileDeleteScope             = "file.delete_scope"
	copyFileOverwrite               = "file.overwrite"
	copyFileOverwriteScope          = "file.overwrite_scope"
	copyFileInvalidIntent           = "file.invalid_intent"
	copyFileInvalidPath             = "file.invalid_path"
	copyFilePathTooLong             = "file.path_too_long"
	copyFileConfirmationUnavailable = "file.confirmation_unavailable"
	copyFileCheckResult             = "file.check_result"
	copyFileCheckBackup             = "file.check_backup"
	copyFileIntentChanged           = "file.intent_changed"
	copyFileManagedUnavailable      = "file.managed_unavailable"
	copyFileManagedRequired         = "file.managed_required"
	copyFileTargetChanged           = "file.target_changed"
	copyFileTargetExists            = "file.target_exists"
	copyFileManagedFailed           = "file.managed_failed"
	copyFileManagedDenied           = "file.managed_denied"
	copyFileDirectoryChanged        = "file.directory_changed"
	copyFileOperationFailed         = "file.operation_failed"
	copyFileUnsupported             = "file.unsupported"
)

var desktopCopy = map[Locale]map[string]string{
	LocaleEN: {
		copyStatusConnecting:       "Connecting to Octop…",
		copyStatusCheckingRuntime:  "Checking the runtime…",
		copyStatusStartingService:  "Starting the Octop service…",
		copyStatusReady:            "Octop is ready",
		copyStatusUsingRuntime:     "Using the existing runtime…",
		copyStatusBackupDatabase:   "Desktop update %s found. Backing up the database…",
		copyStatusInstallingUpdate: "Installing the bundled desktop update…",
		copyStatusUpdatingRuntime:  "Updating the bundled runtime…",
		copyStatusFirstExtract:     "First launch: unpacking the bundled runtime…",
		copyStatusUpdateFailedKeep: "Runtime update failed; using the existing runtime…",
		copyStatusUsingExisting:    "Octop is already running. Opening it…",
		copyErrorBackupFailed:      "Database backup failed before upgrade; the current version was preserved",
		copyErrorUpgradeFailed:     "Desktop runtime upgrade failed; the current version was preserved",
		copyErrorInvalidRuntime:    "Download the desktop installer again to replace the invalid runtime package.",
		copyErrorNoFreePort:        "No free port from %d to %d. Close the program using these ports and try again.",
		copyErrorCannotBind:        "Cannot listen on %s:%d (%s). Check bind_host in config.json.",
		copyWait1Minute:            "1 minute",
		copyWaitNMinutes:           "%d minutes",
		copyWait1Second:            "1 second",
		copyWaitNSeconds:           "%d seconds",
		copyHealthNotReady5xx:      "Octop did not become ready within %s (%s). The service responded but is not ready yet. Try again, or check the terminal logs.",
		copyHealthNotReadyConnect:  "Octop did not become ready within %s (%s). Could not connect — make sure Octop is running.",
		copyHealthNotReady:         "Octop did not become ready within %s (%s). Make sure Octop is running at this address, or check the terminal logs.",
		copyTraySettings:           "Settings",
		copyTrayShowMain:           "Show Octop",
		copyTrayQuit:               "Quit",

		copyFileConfirmTitle:            "Confirm file operation",
		copyFileConfirmMessage:          "Action: %s\nFull path (join the lines without spaces):\n%s\nScope: %s",
		copyFileDelete:                  "Delete",
		copyFileDeleteScope:             "Delete this file.",
		copyFileOverwrite:               "Overwrite",
		copyFileOverwriteScope:          "Replace this file with the generated document.",
		copyFileInvalidIntent:           "Check the file and start a new task.",
		copyFileInvalidPath:             "Choose a path without control characters.",
		copyFilePathTooLong:             "Shorten the path and try again.",
		copyFileConfirmationUnavailable: "Open the desktop window and try again.",
		copyFileCheckResult:             "Check the target file before starting a new task.",
		copyFileCheckBackup:             "Check the target and this backup path: %s",
		copyFileIntentChanged:           "The operation or target changed. Start a new task to confirm it.",
		copyFileManagedUnavailable:      "Install and start the Linux managed file service.",
		copyFileManagedRequired:         "Choose a file in the managed directory.",
		copyFileTargetChanged:           "Choose the target file again.",
		copyFileTargetExists:            "Use a different file name.",
		copyFileManagedFailed:           "Check the managed directory and target file, then try again.",
		copyFileManagedDenied:           "Check the approved device connection and file operation permissions on the configured server.",
		copyFileDirectoryChanged:        "Grant access to the directory again.",
		copyFileOperationFailed:         "Check the target file and directory permissions before trying again.",
		copyFileUnsupported:             "Use a supported desktop client for this file operation.",
	},
	LocaleZH: {
		copyStatusConnecting:       "正在连接 Octop…",
		copyStatusCheckingRuntime:  "正在检查运行环境…",
		copyStatusStartingService:  "正在启动 Octop 服务…",
		copyStatusReady:            "Octop 已就绪",
		copyStatusUsingRuntime:     "正在使用已有运行环境…",
		copyStatusBackupDatabase:   "发现客户端新版 %s，正在备份数据库…",
		copyStatusInstallingUpdate: "正在安装客户端内置新版…",
		copyStatusUpdatingRuntime:  "正在更新内置运行环境…",
		copyStatusFirstExtract:     "首次启动，正在解压内置运行环境…",
		copyStatusUpdateFailedKeep: "更新内置运行环境失败，继续使用已有运行环境…",
		copyStatusUsingExisting:    "Octop 已在运行，正在打开…",
		copyErrorBackupFailed:      "升级前数据库备份失败，已保留当前版本",
		copyErrorUpgradeFailed:     "客户端运行环境升级失败，已保留当前版本",
		copyErrorInvalidRuntime:    "内置运行环境包无效，请重新下载桌面安装包。",
		copyErrorNoFreePort:        "从 %d 到 %d 没有空闲端口，请关闭占用这些端口的程序后再试。",
		copyErrorCannotBind:        "无法在 %s:%d 上监听（%s）。请检查 config.json 里的 bind_host。",
		copyWait1Minute:            "1 分钟",
		copyWaitNMinutes:           "%d 分钟",
		copyWait1Second:            "1 秒",
		copyWaitNSeconds:           "%d 秒",
		copyHealthNotReady5xx:      "Octop 服务未在%s内就绪（%s）。服务已响应但尚未就绪，请稍后再试，或查看终端日志。",
		copyHealthNotReadyConnect:  "Octop 服务未在%s内就绪（%s）。目前无法连接该地址，请确认 Octop 正在运行。",
		copyHealthNotReady:         "Octop 服务未在%s内就绪（%s）。请确认本机已启动 Octop，且地址、端口正确；也可查看终端日志。",
		copyTraySettings:           "设置",
		copyTrayShowMain:           "显示 Octop",
		copyTrayQuit:               "退出",

		copyFileConfirmTitle:            "确认文件操作",
		copyFileConfirmMessage:          "动作：%s\n完整路径（各行直接拼接）：\n%s\n影响范围：%s",
		copyFileDelete:                  "删除",
		copyFileDeleteScope:             "删除此文件。",
		copyFileOverwrite:               "覆盖",
		copyFileOverwriteScope:          "用生成的文档替换此文件。",
		copyFileInvalidIntent:           "请核对文件并新建任务。",
		copyFileInvalidPath:             "请选择不含控制字符的路径。",
		copyFilePathTooLong:             "请缩短路径后重试。",
		copyFileConfirmationUnavailable: "请打开桌面窗口后重试。",
		copyFileCheckResult:             "请核对目标文件后再新建任务。",
		copyFileCheckBackup:             "请核对目标文件和此备份路径：%s",
		copyFileIntentChanged:           "操作或目标已改变，请新建任务并确认。",
		copyFileManagedUnavailable:      "请安装并启动 Linux 受管文件服务。",
		copyFileManagedRequired:         "请选择受管目录中的文件。",
		copyFileTargetChanged:           "请重新选择目标文件。",
		copyFileTargetExists:            "请使用新文件名。",
		copyFileManagedFailed:           "请核对受管目录和目标文件后重试。",
		copyFileManagedDenied:           "请核对已批准的设备连接和服务器上的文件操作权限。",
		copyFileDirectoryChanged:        "请重新授权目标目录。",
		copyFileOperationFailed:         "请核对目标文件和目录权限后重试。",
		copyFileUnsupported:             "请使用支持此文件操作的桌面客户端。",
	},
}

func desktopText(locale Locale, key string, args ...any) string {
	msg := lookupDesktopCopy(locale, key)
	if len(args) == 0 {
		return msg
	}
	return fmt.Sprintf(msg, args...)
}

func lookupDesktopCopy(locale Locale, key string) string {
	if msgs := desktopCopy[locale]; msgs != nil {
		if msg, ok := msgs[key]; ok {
			return msg
		}
	}
	if msg, ok := desktopCopy[LocaleEN][key]; ok {
		return msg
	}
	return key
}
