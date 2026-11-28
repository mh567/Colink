# Colink 桌面客户端（Electron）

新版桌面复用现有控制台的静序、雾蓝界面，在同一窗口和输入框完成聊天、附件发送与本次本机材料读取。启动、进程管理、安装和更新路线参考 TencentCloud/Octop `v1.0.3b7`（MIT），使用锁定的 Electron 45 与 electron-builder 26.18；Python 运行库继续沿用仓库锁文件。

| 路径 | 用途 |
|------|------|
| [`src/main/`](src/main/) | 主窗口、服务连接、随包服务进程和本机文件边界 |
| [`src/preload/`](src/preload/) | 隔离的原生桥，仅暴露服务配置与本次文件范围操作 |
| [`src/renderer/`](src/renderer/) | 首次企业服务连接页 |
| [`portable/`](portable/) | 随包 Python 与运行依赖，仍保留 `Octop-portable-*` 名称 |
| [`package-release.sh`](package-release.sh) | 控制台构建、便携包核验和 Electron 安装包构建 |

`src/` 中原有 Go/Wails 文件保留供历史任务参考，不会打进 Electron 应用。本目录也不是服务端远程桌面模块 `src/octop/infra/control/desktop`。

## 开发与启动

使用 Node.js 24，在企业服务已运行后执行（示例为本机服务）：

```bash
cd desktop/src
npm ci
npm test
COLINK_SERVER_URL=http://127.0.0.1:8088 npm start
```

不指定 `COLINK_SERVER_URL` 时，使用已保存的企业地址；首次启动尝试随包服务，缺少运行环境时显示连接页。也可通过文件菜单切换企业 HTTPS 地址，可包含反向代理路径前缀。仅环回地址允许 HTTP，不跳过证书检查。首次登录沿用服务端账号流程，普通聊天无需设备审批或目录绑定。

安装包也可启动随包服务：只使用 `resources/portable` 内的 Python 和依赖，随机绑定本机端口，健康检查通过后打开控制台；缺少运行环境会明确报错。开发运行的对应目录为 `desktop/portable/release/Octop-<platform>`。退出应用时回收它启动的服务进程树。

## 数据与文件范围

- 默认数据目录为 `~/.colink-v1`，`profile/` 保存 Electron 会话数据，`server/` 保存随包服务数据，`connection.json` 保存所选服务地址。测试可用 `COLINK_DESKTOP_HOME` 指定另一个隔离目录。
- 不沿用 `OCTOP_HOME`、`~/.octop` 或旧 Wails 数据；拒绝指向旧目录的重定向，不迁移旧账号、会话、目录授权、任务或文件。
- 附件经确认才上传原件到企业服务器。“在本机使用”只读取本次选择的 UTF-8 TXT、MD、CSV，文字随本条消息发送，原件留在本机；两者可在同一条消息中混用。
- 本机范围绑定当前账号、专家和会话，单次使用；最多 20 个文件、单文件 1 MiB、合计 60,000 字符。目录包含不支持文件、符号链接、替换文件或超限材料时明确失败。
- 移除、取消、换会话、退出登录、网络离线或权限复核失败会撤销未完成的读取。聊天连接短暂重连不单独撤销尚未使用的范围，发送时仍重新核验权限。移除范围不会撤回已经进入聊天的文字。普通附件不自动入知识库，也不启用默认关闭的记忆采集。

页面启用上下文隔离与渲染沙箱，不暴露 Node.js、任意路径读写或命令执行。原生选择器确定文件范围，主进程再次向当前服务核对账号、专家和已有会话权限。Office 文件处理和受控写入仍待后续专项接入，不能用本次文本读取代替其验收。

## 构建、安装与更新

在对应架构的原生系统或虚拟机运行：

```bash
desktop/package-release.sh
# 复用已构建的便携包，仍执行导入和包内容校验：
desktop/package-release.sh darwin-arm64 --reuse-portable
```

脚本构建控制台、生成并校验便携运行环境，再将它置于安装包的 `resources/portable`。输出位于 `desktop/src/release/`，名称为 `Colink-desktop-<platform>-<version>`：Windows 为 NSIS `.exe`，macOS 为 `.dmg`，Linux 为 `.deb`。`amd64` 对应 Electron 的 `x64`。版本来自 `pyproject.toml`；内部应用版本按 npm 语义版本格式转换，产物名称保留原版本。便携包与 Python 包继续使用原来的 `Octop`/`octop` 标识。

[桌面构建工作流](../.github/workflows/octop-desktop.yml)沿用六个平台的原生 runner，调用同一脚本。可先退出应用，再离线安装企业提供的新包；也可按下面的配置检查企业桌面更新。NSIS 不删除应用数据目录，其他安装方式也没有清理用户数据的脚本。用户个人材料不会复制到安装目录。安装、升级、卸载的实际行为须用对应平台产物验证。

### 企业桌面更新

管理员在启动桌面进程的环境中设置 `COLINK_UPDATE_MANIFEST_URL`，指向企业 HTTPS 静态 JSON 清单。未配置时，文件菜单的“检查桌面更新”会提示联系管理员；没有默认地址或上游公网回退源。此入口更新 Electron 安装包，控制台已有的 Python 服务更新接口不承担桌面更新。

例如，管理员将以下示例清单和现有构建产物放在同一 HTTPS 来源，再设置 `COLINK_UPDATE_MANIFEST_URL=https://updates.example.com/colink/releases.json`；示例版本 `1.0.4` 不表示当前发行版本：

```json
{
  "success": true,
  "data": {
    "client": {
      "stable": {
        "tag": "v1.0.4",
        "assets": {
          "win_x64": {
            "name": "Colink-desktop-windows-amd64-1.0.4.exe",
            "ext": "exe",
            "url": "./Colink-desktop-windows-amd64-1.0.4.exe"
          }
        }
      }
    }
  }
}
```

清单沿用上游的 `data.client.stable` 结构，管理员为实际发布的平台补充 `assets`。`name` 必须匹配构建产物名 `Colink-desktop-<platform>-<version>.<ext>`，版本对应 `tag` 去掉开头的 `v`。只提供比当前应用新的版本，不降级。

| 清单键 | platform | ext |
|--------|----------|-----|
| `mac_arm` | `darwin-arm64` | `dmg` |
| `mac_intel` | `darwin-amd64` | `dmg` |
| `win_arm` | `windows-arm64` | `exe` |
| `win_x64` | `windows-amd64` | `exe` |
| `linux_arm` | `linux-arm64` | `deb` |
| `linux_x64` | `linux-amd64` | `deb` |

清单和安装包必须同源、直接返回内容；拒绝 HTTP、带账号口令的 URL 和重定向。读取清单、下载和启动安装器均在主进程完成，网页没有更新原语，也不会向更新源转发登录令牌或会话 Cookie。安装包仅从管理员配置的 HTTPS 发布地址获取，当前清单与上游一样不提供包摘要字段。

用户通过原生菜单检查并确认后才下载。取消或下载失败保留当前服务；下载完成后撤销本机文件范围、暂停页面并回收本壳启动的服务进程树，确认退出后才交给系统安装器。远端企业服务不停止。Windows 启动 `.exe`，macOS 打开 `.dmg`，Linux 交系统软件安装程序打开 `.deb`；Linux 缺少对应程序时明确报错并恢复原连接。

安装器打开失败时，重启原随包服务或重连原企业地址；恢复失败会提示重新连接。成功交接后应用退出，后续安装由用户和系统完成。此恢复沿用上游的安装器交接失败恢复，不包含安装完成后的自动回滚。整个过程不导入、删除旧版目录或用户材料。

## 验证范围

### 应用图标与欢迎动效

Electron 安装包、运行窗口与 Dock 使用雾蓝渐变底的白色协同环。图标母稿为 [`colink-app-icon.svg`](../dashboard/public/colink-app-icon.svg)，保持 256 × 256 画布、56 圆角及原有双弧轮廓。在 macOS 的 `desktop/src` 执行 `npm run icons`，从 SVG 逐尺寸渲染；复用现有 Electron、resedit 和系统 iconutil，其他平台直接使用已提交的图标打包。

| 平台 | 随包图标尺寸（像素） |
| --- | --- |
| Windows ICO | 16、20、24、32、40、48、64、96、128、256 |
| macOS ICNS | 16、32、128、256、512 及各自的 Retina 2 倍尺寸，最大 1024 |
| Linux PNG | 16、24、32、48、64、96、128、256、512、1024 |

macOS 各尺寸保持 1024 画布内居中 824 图形的留白；正式包由系统使用多尺寸 ICNS，避免运行时单张 PNG 覆盖。Windows 运行窗口使用同一多尺寸 ICO，Linux 安装包使用图标目录。

新对话欢迎页使用透明背景协同环，短弧约 1 秒归位后静止；为另一位专家新建对话时重新播放一次。未配置模型和没有专家的空状态显示静态标志，模型列表加载完成时不会重播。输入、发送和离开无需等待，切换浅深主题不重播；系统开启减少动态效果时直接显示静态标志。图形与动画随包提供，不访问公网。

验收时逐层解码 ICO/ICNS，以实际像素检查各尺寸双弧间隙，尤其是 16/20/24/32/40/48/64/96；同时检查 125%、150%、200% 缩放。目标系统安装后核对应用列表、任务栏或 Dock 图标。浅深主题各进入新对话，动画期间输入、发送和离开；开启减少动态效果后重新进入，并断公网复查。各平台安装包仍由 #25、#74、#75 复核，#30 汇总。

### 自动检查与桌面操作

```bash
cd desktop/src
npm test
npm run typecheck
```

Windows 进程回收测试使用已构建的 `desktop/portable/release/Octop-windows-<arch>/runtime/python.exe`，也可用环境变量 `COLINK_TEST_PYTHON` 指定现有 Python 的绝对路径。缺少运行环境时该专项明确跳过，不能记为已验证。

更新测试使用隔离临时目录和模拟下载响应，覆盖来源与版本校验、取消、下载失败、进程退出、安装器交接及恢复失败。系统安装器边界使用替身，不执行真实安装；Windows、macOS、Linux 的系统安装和升级仍须以对应发行包单独验收。

使用隔离账号和示例文件，在真实 Electron 窗口检查：直接聊天；附件拒绝与确认；本机范围选择、移除、取消和混用；换会话与退出账号；浅深主题、中文输入、键盘和缩放。预置旧目录中的账号、会话、授权、任务和文件，核对首次启动及更新后均未导入、改写或删除。普通网页不得取得本机范围桥。

构建成功不能替代安装运行验收。Windows x64、Linux x64、Linux ARM64 分别使用同架构目标系统或虚拟机验证，复用[平台测试分工](../docs/agents/workflow.md#平台测试分工)和[便携包验证](portable/README.md#本机验证)。macOS 开发结果不代表上述目标平台已通过；实际命令、截图与未验证项随 PR 记录。

## 旧 Wails 客户端参考

以下保留旧客户端的构建与专项任务说明，使用 `~/.octop`、旧设备登记和目录授权。新版 Electron 不读取或迁移这些数据，也不加载旧原生桥；下文的 Wails 包名、更新和设备任务不代表新版已实现。

## Build the Wails shell

Run these from **`desktop/src`** (that directory contains `Taskfile.yml` and
`build/config.yml`). Requires **Go 1.25+**, [Wails v3](https://v3.wails.io/)
`v3.0.0-beta.13`.

```bash
go install github.com/wailsapp/wails/v3/cmd/wails3@v3.0.0-beta.13
cd desktop/src
go mod tidy
wails3 build            # development binary under desktop/src/bin/
wails3 task package ARCH=arm64 VERSION=<version> \
  PORTABLE_ZIP=../portable/release/Octop-portable-darwin-arm64-<version>.zip
```

Dev against an already-running Octop (skips the bundled green zip):

```bash
cd desktop/src
OCTOP_DESKTOP_URL=http://127.0.0.1:8088 wails3 dev
```

Without `OCTOP_DESKTOP_URL`, first launch uses `~/.octop/portable/` if valid,
otherwise extracts the matching zip shipped with the desktop package (embedded
in the Windows and Linux binaries, under `Contents/Resources` on macOS). The
Wails shell never downloads Octop. For local runtime debugging, set
`OCTOP_DESKTOP_PORTABLE_ZIP=/absolute/path/Octop-portable-<plat>-<version>.zip`.
On later launches, a newer bundled portable version replaces the extracted
runtime after creating a consistent SQLite backup under `~/.octop/backups/`.
The upgraded Octop process then applies the normal database migrations during
startup. Newer extracted runtimes are never downgraded; PostgreSQL remains
externally managed and is not copied by the desktop shell.

GitHub Release names follow `Octop-<kind>-<os>-<arch>-<version>.<ext>`:

- Desktop GUI: `Octop-desktop-<plat>-<version>.dmg` (macOS; open and drag
  `Octop.app` into Applications), `.exe` (Windows NSIS installer — copies
  into `Program Files\Octop` and creates Start Menu + desktop shortcuts),
  `.tar.gz` (Linux)
- Green runtime zip: `Octop-portable-<plat>-<version>.zip`
- PyPI wheels stay `octop-<version>-py3-none-any.whl` (PEP 427)

The Linux tar.gz contains the GUI binary and the optional managed file service in
`fileops/`. The GUI embeds its portable runtime. Runtime upgrades remain owned by Octop:
the shell sets `OCTOP_GREEN_PACKAGES`, so `octop update` upgrades the extracted
`packages/` directory through Octop's existing `--target` logic.

Linux links GTK4 + WebKitGTK 6 by default. When only GTK3 + WebKitGTK 4.0 is
installed (Kylin V10 SP1 2503 ships WebKitGTK 2.38.6), the Linux build task
selects the `gtk3 webkit238` compatibility build automatically; the printed
build command shows the tags. That build needs `gtk+-3.0`, `webkit2gtk-4.0` and
`libsoup-2.4` development packages. WebKitGTK 2.38 cannot deliver
custom-scheme request bodies, so the bundled shell pages must use the native
IPC bridge instead of the HTTP runtime transport. macOS 12+.

## Windows 本机文件确认

在本机文档任务面板授权读写目录后，选择覆盖或删除。例如，覆盖示例文件 `周报.docx` 时，确认框会显示动作、完整路径和影响范围。点击取消后核对原件，点击确认后核对更新内容。同一任务重试时可查看已记录的结果。如果任务提示备份路径，先核对目标文件和备份，再新建任务。

从 `desktop/src` 在 Windows 原生环境运行 `go test -count=1 ./...`，再用同一源码构建的桌面客户端检查示例文件。确认期间调换同名文件或撤销权限时，核对两个文件的内容是否保持原样。

## Linux 受管文件目录

在安装了 systemd、系统消息总线和 Polkit 的 Linux 桌面上，管理员将对应架构的可信桌面包解压到仅管理员可写的目录，并运行 `sudo ./fileops/install.sh https://colink.example.com/office`。示例网址应替换为员工桌面客户端连接的 Colink 服务器地址，并包含相同的反向代理路径前缀。安装脚本将可信地址保存在管理员拥有的 `/etc/colink-fileops/server-url`，并使用包内二进制和配置。

受管文件服务必须能通过系统信任的证书连接该 HTTPS 服务器，以核对已批准的设备会话和文件动作权限。仅部署在内网也可使用受系统信任的企业证书。管理员可用 `systemctl status colink-fileops` 检查服务，桌面程序仍以员工的普通账户启动。

在本机文档任务面板授权受管目录后，先向该目录新建文件，再选择其中的文件覆盖或删除。例如，生成 `周报.docx` 后选择覆盖，系统弹窗会列出目标路径、覆盖动作和影响范围。点击取消后，核对原文件内容是否保持不变。普通授权目录继续用于读取和新建文件。

在目标 Linux 桌面验收时，从 `desktop/src` 运行 `go test -count=1 ./internal/fileops`，再安装同一源码构建的桌面包。用受管目录内的临时文件逐项检查。

- 覆盖和删除各取消一次，`sha256sum` 输出保持一致。
- 分别确认覆盖和删除，只有覆盖目标的内容发生变化，只有删除目标消失。同一任务重试返回原结果。
- 弹窗打开后撤销当前设备或动作权限，再确认操作，目标文件保持原样。

可在专用 Linux 测试用户下，从 `desktop/src` 运行 `go test -run '^TestLiveManagedDirectoryReturnsAbsolutePath$' .`（按目标平台构建配置添加构建标签），验证设备接入后能打开受管目录。配置 `COLINK_FILEOPS_TEST_SERVER`（与已安装服务一致的测试 HTTPS 地址）、`COLINK_FILEOPS_TEST_USERNAME`、`COLINK_FILEOPS_TEST_PASSWORD_FILE` 和 `COLINK_FILEOPS_TEST_DEVICE_KEY_FILE`。设备密钥必须是专用测试服务器上已审批、当前未连接的独立设备密钥，使用权限为 `0600` 的绝对路径。测试不创建、覆盖或删除办公文件；上述确认流程仍须在桌面上验收。

## Icons

| File | Used for | Rule |
|------|----------|------|
| `src/build/appicon.png` | Windows `.ico`, Linux | Full-bleed 512x512 artwork |
| `src/build/appicon-macos.png` | macOS `.icns` | 1024x1024 canvas, artwork 824x824 centred |
| `src/assets/tray-icon.png` | Tray + app icon on Windows/Linux | Full-bleed |
| `src/assets/tray-icon-template.png` | macOS menu bar | 88px canvas, 64px black-on-transparent glyph |

macOS sizes both surfaces to a fixed box, so the padding has to live in the
artwork: the Dock follows Apple's 824/1024 icon grid, and Wails scales the menu
bar image to the full `NSStatusBar` thickness (22pt) where the glyph should be
~16pt. Full-bleed sources on either surface render a size bigger than every
other app. On macOS the Dock icon comes from the bundle's `icons.icns` only —
see `applyAppIcon` in `src/icons_darwin.go`.

## 本机 XLSX 任务

设备页的本机文档任务可选择 XLSX。Excelize v2.11.0 及其依赖编译进桌面程序，运行时不下载库、不启动 Python 或执行模型脚本；开发及构建仍须预取 `go.mod`/`go.sum` 的依赖。新库最低 Go 1.25，与当前桌面工具链一致。

许可全文位于 [third-party-xlsx.txt](src/assets/third-party-xlsx.txt)，随 `assets/*` 嵌入桌面二进制，可由本地壳资源路径 `/third-party-xlsx.txt` 读取。实际目标平台离线安装与 Office 打开编辑仍需单独验收。支持范围及权限见[本机 XLSX 接口](../docs/api.md#本机-xlsx-任务)。

## 本机 PPTX 任务

设备页选择 PPTX，在只读目录选取示例文件 `项目进展.pptx`，并在读写目录生成 `项目汇报.pptx`。从任务记录查看路径，用演示文稿编辑器打开成果，核对标题和要点后修改并保存。权限和格式范围见[本机 PPTX 接口](../docs/api.md#本机-pptx-任务)。

便携包随附本机处理程序，沿用 `uv.lock` 的 [python-pptx 1.0.2 及其 MIT 许可](https://github.com/scanny/python-pptx/blob/v1.0.2/LICENSE)。生成技能由 Colink 独立编写，打包时继续移除 Harness 的受限演示文稿材料。使用同一包在断公网目标环境核对安装、中文文件和编辑结果。

安装同一应用版本的新便携包后，请退出正在运行的本机客户端再打开，以加载更新后的运行时。

## 本机浏览器查询与表单操作

桌面客户端复用 [Octop Browser 1.0.0](https://pypi.org/project/octop-browser/1.0.0/) 的截图能力。便携包提供运行程序和 Python 依赖。部署前为 Windows 准备系统 Edge 或 Chrome，为 Linux 或 macOS 准备 Chromium，并配置目标平台的图形库、沙箱支持和中文字体。缺少浏览器或浏览器无法启动时，设备页提示检查本机安装。所用系统浏览器的二进制和更新由其发行方或企业离线软件源提供。

员工从设备页打开专用浏览器，亲自登录内网系统并切到查询结果。以示例订单 `DEMO-42` 为例，选择订单结果标签，输入“还有多少库存”，核对答案与来源地址，必要时在同一标签截图。退出应用并重新连接同一账号、设备和服务器后，重新打开专用浏览器检查原登录态。换另一个员工或设备，检查需要重新登录。撤销设备后再次查询，核对设备页拒绝操作。在浏览器启动过程中撤销权限，核对待访问的业务地址没有被打开。

处理表单时，员工选择标签页、候选表单和提交动作，输入任务说明并查看模型建议。提交前核对字段变化；桌面原生确认窗会再次列出页面和脱敏提交目标、方法、打开位置、表单、提交动作及字段变化。确认后客户端只调用一次所选提交按钮。操作记录不保存字段值。“操作已发出”只表示浏览器点击调用已返回，不表示业务服务成功。若回执丢失或状态显示结果待核对，先检查业务页面，不要重放提交。

附件上传从只读授权目录选择文件，下载写入读写授权目录。员工确认网页动作；下载覆盖已有文件时另行确认。下载仅支持当前页面同源链接。 [接口和权限](../docs/api.md#本机浏览器查询与表单操作)

专用浏览器通过仅本机工作进程继承的管道接收调试命令。便携包须包含对应版本的浏览器工作进程。

此前的 Windows 浏览器基线验证使用 Windows 10 19045 x64 与 Microsoft Edge 系统安装版 157.0.4341.46。隔离示例页通过自动化填入测试凭据，两个临时配置的登录态相互独立，重启后各自保留。调试监听为 `127.0.0.1`，且属于启动的 Edge 进程。`edge://sandbox` 显示渲染进程使用 Lockdown、Untrusted 完整性级别和 AppContainer。随机调试连接上的标签页操作、示例库存读取和截图通过，退出后旧端口不可访问。

此前的 Windows 运行程序读取了示例订单并截图，来源变化或页面出现密码输入框时拒绝读取。临时桌面窗口通过原生网页来源校验和设备会话握手，使用模拟授权与模拟结果验证操作回执。实际企业账号和同一发行包的完整流程仍需验收。

Windows 10 使用系统 Edge 157.0.4341.46 复测了私有管道和浏览器工作进程。测试在独立配置中打开本机示例订单，读到同源嵌入报表且排除了透明文字，生成截图，并拒绝读取密码页。Windows 进程组回归另验证工作进程异常退出后浏览器子进程能够清理。

麒麟前期核验的 Playwright 路径缺少浏览器二进制，启动失败。所选系统 Chromium 路径仍需在麒麟目标桌面上用同一客户端包断公网复测，记录浏览器软件包来源及版本、启动进程、配置目录、私有调试管道、沙箱和真实页面结果。 [麒麟前期核验](../docs/T01-源码许可与麒麟早期核验.md)

### 附件与离线验收

用同一桌面包，在断开公网但可访问测试服务器时验证附件：选择授权目录中的普通文件，拒绝确认时网页不接收文件；确认时网页可读取原始字节，提交结果在网页核对。下载用同源直接链接或链接选择器，核对读写目录里的文件内容；未授权、只读和已撤销的目录不能落盘。下载期间出现同名文件时仍需覆盖确认，取消后原文件保持原样。Windows 和 Linux 分别在原生桌面验收覆盖；Linux 须安装同包的受管目录服务。macOS 可新建，现有确认机制不支持覆盖。浏览器及便携运行时须预先安装。

可用本机浏览器与预装 `octop-browser` 的 Python 复跑回环测试：在 `desktop/src` 设置 `COLINK_TEST_BROWSER_PYTHON` 为 Python 的绝对路径，再执行 `go test -run ^TestLocalBrowserNativeFileTransfer$`。该测试不需要企业凭据；发行包和系统原生确认仍按上面的步骤验收。
