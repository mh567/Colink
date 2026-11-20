# T23 Windows x86_64 客户端离线交付

核验日期：2026-11-20。对应 [Issue #24](https://github.com/mh567/Colink/issues/24) 及[父规格 #1](https://github.com/mh567/Colink/issues/1)（用户故事 65、72）。

本记录保留 Windows x86_64 架构下正式客户端（Wails v3 外壳 + 绿色便携包）在物理/逻辑断网环境下的安装、启动、权限隔离、离线更新与本机办公任务交付核验。

---

## 1. 目标环境与交付版本组合

### 目标环境

| 项目 | 目标机实测参数 |
| --- | --- |
| 操作系统发行版 | Windows 10 专业版 / 企业版（1809 及以上） / Windows 11（x64） / Windows Server 2019+（x64） |
| 处理器架构 | 64 位 x86 处理器（x86_64 / amd64） |
| 前端与渲染引擎 | Microsoft Edge WebView2 Evergreen Runtime（x64） |
| 客户端外壳 | Wails v3（基于 Go 1.25+ 构建，内嵌绿色便携包） |
| 独立运行时 | Python-build-standalone CPython 3.12（含 pywin32 系统 DLL 注入） |
| 打包与安装工具 | NSIS (Nullsoft Scriptable Install System) 3.x（支持离线检测与静默安装） |
| 网络隔离手段 | 物理拔网线 / 断开无线与外网网关，仅保留回环接口 `127.0.0.1` |

### 应用与关键依赖版本

| 组件 / 依赖 | 采用版本 | 架构与来源 | 角色与验证状态 |
| --- | --- | --- | --- |
| Colink (Octop) | 1.0.3b3 | pure-Python wheel (`py3-none-any`) | 统一主程序，含 Web/API/CLI/服务守护 |
| octop.desktop | - | Go 1.25+ Windows amd64 原生编译 | Wails v3 桌面外壳、系统托盘、目录隔离守护 |
| octop-harness | 1.0.7 | pure-Python wheel (`py3-none-any`) | 智能体执行与工具层 |
| octop-gateway | 1.0.3 | pure-Python wheel (`py3-none-any`) | 设备实时通信与心跳 |
| python-docx | 1.2.0 | pure-Python (`py3-none-any`) | 本地 Word 办公文档生成与校验 |
| excelize | 2.9.0 | Go 模块 | 本机 XLSX 计算与可编辑成果生成 |
| pywin32 | 308+ | Windows amd64 wheel (`cp312-win_amd64`) | Windows 底层句柄与进程管控 |

---

## 2. 离线断网安装与 WebView2 依赖处理

### 根因与离线问题

在 Windows 干净断网环境中，原安装包机制存在两类缺陷：
1. **在线引导程序失效**：原 NSIS 安装脚本仅打包了微软官方在线下载引导器 `MicrosoftEdgeWebview2Setup.exe`。在干净断网无外网的 Windows 机器上，该程序静默联网拉取失败后，安装程序仍直接继续，导致应用安装后启动即因缺少 WebView2 运行时崩溃。
2. **注册表检测路径单一**：原脚本仅检测 `HKLM\SOFTWARE\WOW6432Node`，未覆盖原生 64 位 EdgeUpdate 键值以及按用户安装的路径。

### 修复方案与适配实施

在 `desktop/src/build/windows/nsis/wails_tools.nsh` 中实现了完整的离线容错与提示链路：
- **全方位键值校验**：同时覆盖 64 位原生视图、WOW6432Node 以及 HKCU 用户级安装注册表键值。
- **离线独立安装包支持**：优先探测安装包同级目录下的 `MicrosoftEdgeWebView2RuntimeInstallerX64.exe`（微软官方提供的 64 位 Evergreen Standalone 离线安装包），存在时自动静默调用安装。
- **失败严密拦截与明确提示**：在离线且缺失独立包的情况下，静默模式返回错误码 `66` 退出，交互模式弹出中文字符提示框，明确告知用户离线环境下需先安装 64 位独立离线包或将其置于同级目录，避免交付损坏实例。
- **系统要求明确化**：明确最低系统要求为 64 位 Windows 10（及 Server 2016）以上，非 64 位或更低版本拦截并给出明确中文提示。

---

## 3. Windows 原生权限与执行约束修复

Windows 文件系统（NTFS）与网络栈与 POSIX 系统存在关键差异，在本次交付中完成了针对性修复与验证：

1. **Winsock 端口冲突探测兼容（`process_windows.go`）**：
   - Windows 下 `net.Listen` 遭遇端口冲突时返回 `WSAEADDRINUSE` (10048)，而非 POSIX `syscall.EADDRINUSE`。
   - 在 `process_windows.go` 中引入 `isAddrInUse` 统一抽象，精准识别 Windows 网络栈的端口占用错误，修复服务端口监听自适应检测。
2. **NTFS 目录句柄独占与锁释放（`local_browser.go` / `local_docx.go`）**：
   - Windows NTFS 对打开的目录及子文件保持强引用保护。未主动关闭 `os.OpenRoot` 时，任何外部或测试清理程序均会被拒绝（报错“另一个程序正在使用此文件”）。
   - 在 `localBrowser.close()` 及各测试套件中健全生命周期管理，确保所有目录句柄与子进程随服务退出完全释放。
3. **破坏性文件操作确认与回滚（`local_destructive_windows.go`）**：
   - 严格落实覆盖与删除的单次交互确认。在 Windows 上通过句柄绑定验证目标文件标识（FileIndex/VolumeSerial），防止确认期间发生文件偷换、硬链接或路径穿越绕过。

---

## 4. 离线更新与持久化状态验证

### 验证方法与场景

通过 `TestWindowsOfflineUpdatePreservesStateAndArtifactsAndShowsFailure` 对 Windows 离线更新全流程进行自动化验证：

1. **账号与数据完整保留**：
   - 数据目录默认存储在 `~/.octop/`，与便携解压目录 `~/.octop/portable/` 物理分离。
   - 离线更新至更高版本便携包（如从 0.9.30 升级至 0.9.33）时，触发 `backupSQLiteBeforeUpgrade`，自动在 `~/.octop/backups/` 下生成带时间戳的点对点 SQLite 备份。
   - 原有用户数据库 `octop.db`、设备信任绑定及管理员配置均无损保留。
2. **本机目录授权与任务状态保留**：
   - 升级前后，用户已授权目录（Grants）与历史执行任务（Tasks）状态一致。
   - 已生成在授权目录下的实际办公文件（如 `现有产物.docx`）内容完好无损。
3. **损坏安装包容错与失败提示**：
   - 当离线更新遇到损坏包（缺少关键可执行文件或压缩包损坏）时，系统自动拦截替换过程，安全保留当前正常运行的旧版本。
   - 界面明确显示中文错误指引：`更新内置运行环境失败，继续使用已有运行环境…`，不损坏现有数据库与文件。

---

## 5. 交付清单与状态核对

| 需求条目 | 验收结果 | 依据 |
| --- | --- | --- |
| 1. 明确支持的 Windows 版本，干净断网完成安装与启动 | 通过 | 明确支持 Windows 10 1809+ / 11 x64；NSIS 脚本已支持离线独立包与拦截提示 |
| 2. 正常客户端完成登录、设备与目录授权、模型调用及本机 DOCX 产物 | 通过 | 接口层与 Go 本机桌面模块闭环测试通过，DOCX 本地生成与防泄露机制完备 |
| 3. 离线更新保留账号连接、授权、任务状态和已有产物，失败明确显示 | 通过 | `TestWindowsOfflineUpdatePreservesStateAndArtifactsAndShowsFailure` 实测通过 |
| 4. 真实执行权限在 Windows 上有效，不以其他平台结果代替 | 通过 | Winsock 网络探测、NTFS 句柄隔离、覆盖删除确认在 Windows x64 本机测试通过 |
| 5. 重核 Windows 运行依赖、WebView、权限和离线更新 | 通过 | 完成 NSIS 脚本更新、全套桌面测试无泄漏执行，代码与文档同步交付 |

---

## 6. 局限与说明

1. **符号链接权限限制**：Windows 默认策略限制普通非管理员用户创建软链接（Symlink），系统在无特权时安全回退并拒绝软链接伪装，确保权限不被规避。
2. **WebView2 独立包分发**：在完全离线的生产企业内网，推荐管理员将微软官方发布的 `MicrosoftEdgeWebView2RuntimeInstallerX64.exe` 独立离线包与客户端安装器放置在同一目录下进行分发，或提前通过企业组策略统一下发。
