# T23 Windows x86_64 客户端离线交付

核验日期：2026-11-20。对应 [Issue #24](https://github.com/mh567/Colink/issues/24) 及[父规格 #1](https://github.com/mh567/Colink/issues/1)（用户故事 65、72）。

本记录保留 Windows x86_64 架构客户端（Wails v3 外壳 + 便携运行环境）的离线安装适配、Windows 平台权限与网络边界修复、离线升级自动测试结果及剩余实机待验项。当前交付阶段仅完成源码适配与开发机本机单元测试，尚未在干净断网 Windows 实机上完成正式 `.exe` 安装包安装、首次启动及真实内网端到端验收；所有未实测项均按未通过/待测记录。

---

## 1. 目标环境与采用版本组合

### 支持的目标系统

| 项目 | 声明范围 / 适配基线 |
| --- | --- |
| 支持的操作系统版本 | 64 位 Windows 10（1809 / Build 17763 及以上）、Windows 11（x64）、Windows Server 2019 及以上（x64） |
| 处理器架构 | 64 位 x86 处理器（x86_64 / amd64） |
| 前端与渲染引擎 | Microsoft Edge WebView2 Evergreen Runtime（x64） |
| 客户端外壳 | Wails v3（`github.com/wailsapp/wails/v3 v3.0.0-beta.13`，Go 1.25.0 构建） |
| 便携运行时 | Python-build-standalone CPython 3.12.12（随 `desktop/portable/package.sh` 组装） |
| 安装打包工具 | NSIS（Nullsoft Scriptable Install System）3.x，UTF-8（带 BOM 及 `-INPUTCHARSET UTF8`）中英文双语 |

### 关键组件与依赖版本（以 `pyproject.toml`、`uv.lock` 与 `desktop/src/go.mod` 为准）

| 组件 / 依赖 | 锁定版本 | 来源与说明 | 本轮验证状态 |
| --- | --- | --- | --- |
| Colink (`octop`) | `1.0.3b3` | `pyproject.toml` | 源码版本核对；Windows 正式打包产物待实测 |
| `octop.desktop` | `Go 1.25.0` / `wails v3.0.0-beta.13` | `desktop/src/go.mod` | Windows x64 本机 `go test ./...` 与 `go vet ./...` 通过；NSIS `.exe` 编译与实机安装待测 |
| `octop-harness` | `1.0.7` | `uv.lock` | 锁文件核对；Windows 便携包断网实跑待测 |
| `octop-gateway` | `1.0.3` | `uv.lock` | 锁文件核对；Windows 便携包断网实跑待测 |
| `octop-memory` | `1.0.3` | `uv.lock` | 锁文件核对；Windows 便携包断网实跑待测 |
| `octop-browser` | `1.0.0` | `uv.lock` | 锁文件核对；Windows 断网浏览器资源与实跑待测 |
| `python-docx` | `1.2.0` | `uv.lock` | 锁文件核对；Go 本机 DOCX 读写单元测试通过；客户端完整任务闭环待测 |
| `python-pptx` | `1.0.2` | `uv.lock` | 锁文件核对；便携包断网实跑待测 |
| `reportlab` + `NotoSansSC-Regular.ttf` | `4.4.9` | `uv.lock` + `desktop/pdf/` | 锁文件与打包脚本核对；便携包断网实跑待测 |
| `rapidocr`（`local-ocr`） | `3.9.2` | `uv.lock` + `desktop/ocr/prepare.py` | 锁文件核对；原生 Windows 便携包 OCR 离线模型组装待测 |
| `excelize` | `v2.11.0` | `desktop/src/go.mod` | Windows x64 本机 Go 单元测试通过 |
| `pywin32` | `312` | `uv.lock` | 锁文件核对；便携包断网实跑待测 |

---

## 2. 离线断网安装与 WebView2 依赖适配

### 根因

原 NSIS 安装脚本（[wails_tools.nsh](../desktop/src/build/windows/nsis/wails_tools.nsh) 与 [project.nsi](../desktop/src/build/windows/nsis/project.nsi)）在离线环境存在以下问题：
1. 仅内嵌在线引导器 `MicrosoftEdgeWebview2Setup.exe`，无外网时下载静默失败且不拦截安装。
2. 仅检测 `HKLM\SOFTWARE\WOW6432Node`（未覆盖原生 64 位键值与 `HKCU`），且未排除微软卸载残留占位值 `"0.0.0.0"`。
3. `${AtLeastWin10}` 未校验 Build 号，放行低于 Windows 10 1809 / Windows Server 2019 的旧版本。

### 适配改动

在 [wails_tools.nsh](../desktop/src/build/windows/nsis/wails_tools.nsh)、[project.nsi](../desktop/src/build/windows/nsis/project.nsi) 与 [Taskfile.yml](../desktop/src/build/windows/Taskfile.yml) 中完成以下调整：
- **最低系统版本校验**：通过 `${AtLeastWin10}` 与 `${AtLeastBuild} 17763` 明确限定 Windows 10 1809 / Windows Server 2019 及以上。
- **双语与编码保证**：`project.nsi` 使用带 UTF-8 BOM 编码及 `LangString` 提供中英文提示，`wails_tools.nsh` 保持纯 ASCII 兜底文案，`Taskfile.yml` 调用 `makensis -INPUTCHARSET UTF8`。
- **完整注册表校验**：抽取 `wails.readWebView2Version` 宏，统一检查 64 位视图下的 `HKLM\SOFTWARE\WOW6432Node`、`HKLM\SOFTWARE` 及 `HKCU\Software` 的 `pv` 键值，排除空字符串与 `"0.0.0.0"`。
- **离线 WebView2 安装链路**：按目标架构匹配独立包名（`MicrosoftEdgeWebView2RuntimeInstallerX64.exe` / `MicrosoftEdgeWebView2RuntimeInstallerARM64.exe`），依次尝试：(1) 安装程序同级目录离线包；(2) 构建时通过 `WEBVIEW2_OFFLINE_INSTALLER`（`-DARG_WEBVIEW2_OFFLINE_INSTALLER`）内嵌的独立离线包；(3) 仅在未指定离线包时回退至在线引导器。
- **失败拦截与退出码**：安装尝试后再次复检注册表，仍缺失时统一设置非零退出码 `66` 并中止安装；交互模式按当前安装语言弹出可操作提示。

---

## 3. Windows 原生权限、网络与句柄修复

1. **Winsock 端口冲突探测（[process_windows.go](../desktop/src/process_windows.go) / [process_unix.go](../desktop/src/process_unix.go) / [process.go](../desktop/src/process.go)）**：
   - Windows 上 `net.Listen` 端口占用返回 `windows.WSAEADDRINUSE`（10048），而非 POSIX `syscall.EADDRINUSE`。新增跨平台 `isAddrInUse` 抽象修复端口占用识别。
2. **NTFS 目录句柄与共享归属清理（[local_browser.go](../desktop/src/local_browser.go) / [main.go](../desktop/src/main.go)）**：
   - 区分 `localBrowser` 自建与外部注入的 `localDOCX` 所有权（`newLocalBrowserWithDOCX`），防止 `localBrowser.close()` 越权关闭共享实例；在 `App.ServiceShutdown()` 中显式关闭 `a.docx`，并在测试中补齐 `t.Cleanup(worker.close)`，消除 Windows NTFS 目录句柄残留锁。
3. **PDF 目录替换边界测试（[pdf_test.go](../desktop/src/internal/fileops/pdf_test.go)）**：
   - 在 Windows 上正面断言 `os.OpenRoot` 持有目录句柄期间 `os.Rename` 被系统拒绝，替代中途 `t.Skip`。

---

## 4. 离线更新、状态保留与失败提示

1. **失败回退持久提示（[main.go](../desktop/src/main.go)）**：
   - `App.boot()` 捕获 `ensurePortableAtStartup` 的回退状态（`copyStatusUpdateFailedKeep`），在界面切换到工作台后通过 `warnUpgradeFallback` 弹出原生告警对话框（标题复用 `copyErrorUpgradeFailed`），避免被 `copyStatusStartingService` / `copyStatusReady` 覆盖导致用户不可见。
2. **自动化单元测试（[portable_upgrade_test.go](../desktop/src/portable_upgrade_test.go)）**：
   - `TestPortableOfflineUpgradePreservesStateAndNotifiesOnFailure` 验证：
     - 升级前使用本机 Python 真实 `sqlite3` 执行 `runSQLiteBackup` 备份 `octop.db` 并校验原库与备份库中账号、设备记录一致（无本机 Python 时回退为备份钩子校验）；
     - 升级前后已授权目录、历史任务状态与经 `makeDOCX` 生成、经 `readDOCX` 回读的真实 `.docx` 文件内容完好；
     - 损坏便携包触发安全回退并保留旧版本与文件，且 `warnUpgradeFallback` 收到预期中英文标题与正文提示。
   - `TestAppShutdownClosesSharedDOCXWithoutBrowserClosingIt` 验证 `localBrowser.close()` 不关闭共享 `localDOCX`，仅 `App.ServiceShutdown()` 负责关闭。

---

## 5. 交付清单与状态核对

| 需求条目 | 当前状态 | 依据与未验证项 |
| --- | --- | --- |
| 1. 明确支持的 Windows 版本，干净断网完成安装与启动 | 部分 | 已明确支持版本并完成 NSIS 离线探测、双语提示与 `WEBVIEW2_OFFLINE_INSTALLER` 构建参数适配；本机未装 `makensis`，正式 `.exe` 编译、干净断网 Windows 实机安装及首次启动**待测** |
| 2. 正常客户端完成登录、设备与目录授权、模型调用及本机 DOCX 产物 | 待测 | Go 本机 DOCX 读写、目录授权与测试通过；从正式 Windows 客户端完成登录、设备审批、真实内网模型调用与端到端本机 DOCX 产物**待测** |
| 3. 离线更新保留账号连接、授权、任务状态和已有产物，失败明确显示 | 部分 | `TestPortableOfflineUpgradePreservesStateAndNotifiesOnFailure` 已覆盖真实 SQLite 备份、目录授权/任务状态/真实 DOCX 保留及原生失败弹窗钩子；真实 NSIS 覆盖安装升级、应用启动后真实账号重连与数据库迁移**待测** |
| 4. 真实执行权限在 Windows 上有效，不以其他平台结果代替 | 部分 | Windows x64 本机全量 Go 测试（含 `WSAEADDRINUSE`、NTFS 目录锁防重命名、`local_destructive_windows.go` 句柄校验）全部通过；真实客户端交互弹窗与普通用户受限环境实机演练**待测** |
| 5. 重核 Windows 运行依赖、WebView、权限和离线更新 | 部分 | 已按 `go.mod` 与 `uv.lock` 核对版本并修复 NSIS 与桌面模块问题；含 OCR、PDF 字体、PPTX 与 `octop-browser` 的完整 Windows 便携包组装与断网复验**待测** |

---

## 6. 正式候选包实机验收步骤

在具备 `makensis`、`wails3` 与目标干净 Windows x64 虚拟机（无公网路由）的环境中完成以下验收后，方可确认工单全部通过：

1. **组装完整离线包**：按[便携包说明](../desktop/portable/README.md)在原生 Windows x64 环境构建含 `local-ocr`、PDF 字体、PPTX 与浏览器资源的便携包，运行 `desktop/portable/verify_imports.py` 核对依赖；下载微软官方 `MicrosoftEdgeWebView2RuntimeInstallerX64.exe`，通过 `WEBVIEW2_OFFLINE_INSTALLER=<路径>` 内嵌或置于安装器同级目录。
2. **干净断网安装与首次启动**：在未预装 WebView2 的干净断网 Windows 10 1809+ / Windows 11 虚拟机中分别验证：(a) 缺失离线包时静默安装返回退出码 `66`、交互安装弹出清晰提示并中止；(b) 提供独立离线包后完成安装与首次启动，确认无外网请求。
3. **端到端业务闭环与权限边界**：仅连通隔离企业内网服务端，用正式客户端登录、审批本人设备、授权读写/只读目录、调用真实内网模型生成可打开可编辑的 DOCX，并在未授权目录、只读目录写入和拒绝确认场景验证实际拒绝。
4. **真实离线覆盖升级与失败回退**：以更高版本安装包执行离线升级，验证账号连接、目录授权、任务状态、已有产物与 SQLite 数据库自动备份完整保留；以损坏便携包复验启动回退且原生告警弹窗明确可见。
