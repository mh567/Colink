# Octop desktop (Wails v3 + green portable)

All desktop-client code lives here. This is **not** `src/octop/infra/control/desktop`
(remote desktop streaming).

| Path | Role |
|------|------|
| [`portable/`](portable/) | Green zip packaging (was `scripts/green/`) |
| [`src/`](src/) | Wails v3 shell: load bundled zip, spawn Octop, tray/settings |
| [`package-release.sh`](package-release.sh) | Native end-to-end portable + Wails release build |

## Data directory

Same as the Octop CLI/server default:

- `OCTOP_HOME` → `~/.octop` (or the existing `OCTOP_HOME` env)
- Green runtime extract → `~/.octop/portable/`
- Shell prefs → `~/.octop/desktop-settings.json`

## Build green zip

From repo root:

```bash
make -f desktop/portable/Makefile green
```

CI: `.github/workflows/octop-desktop.yml` builds native platform/arch variants.
`v*` tags and `workflow_dispatch` (platforms=`all`) run all six; pull requests
that touch `desktop/` build `darwin-*` and `windows-*` (amd64 + arm64). Each job
first creates the green zip and then packages the matching Wails application.

## Build a complete desktop release

Run the end-to-end script on the matching native host. It builds the Dashboard,
creates and verifies the portable runtime, embeds it into Wails, and produces
the final native package:

```bash
desktop/package-release.sh
# Reuse an existing desktop/portable/release/Octop-portable-<plat>-<version>.zip:
desktop/package-release.sh darwin-arm64 --reuse-portable
```

Rebuild without `--reuse-portable` if the archive's presentation or browser worker differs from the current checkout.

Wails requires native packaging, so all six variants are produced by the CI
matrix on macOS, Linux, and Windows runners rather than cross-compiled locally.
Windows packaging also needs [NSIS](https://nsis.sourceforge.io/) (`makensis`)
so the `.exe` is an installer rather than a portable single-file binary.

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

## 本机浏览器只读查询

桌面客户端复用 [Octop Browser 1.0.0](https://pypi.org/project/octop-browser/1.0.0/) 的截图能力。便携包提供运行程序和 Python 依赖。部署前为 Windows 准备系统 Edge 或 Chrome，为 Linux 或 macOS 准备 Chromium，并配置目标平台的图形库、沙箱支持和中文字体。缺少浏览器或浏览器无法启动时，设备页提示检查本机安装。所用系统浏览器的二进制和更新由其发行方或企业离线软件源提供。

员工从设备页打开专用浏览器，亲自登录内网系统并切到查询结果。以示例订单 `DEMO-42` 为例，选择订单结果标签，输入“还有多少库存”，核对答案与来源地址，必要时在同一标签截图。退出应用并重新连接同一账号、设备和服务器后，重新打开专用浏览器检查原登录态。换另一个员工或设备，检查需要重新登录。撤销设备后再次查询，核对设备页拒绝操作。在浏览器启动过程中撤销权限，核对待访问的业务地址没有被打开。 [接口和权限](../docs/api.md#本机浏览器只读查询)

专用浏览器通过仅本机工作进程继承的管道接收调试命令。便携包须包含对应版本的浏览器工作进程。

此前的 Windows 浏览器基线验证使用 Windows 10 19045 x64 与 Microsoft Edge 系统安装版 157.0.4341.46。隔离示例页通过自动化填入测试凭据，两个临时配置的登录态相互独立，重启后各自保留。调试监听为 `127.0.0.1`，且属于启动的 Edge 进程。`edge://sandbox` 显示渲染进程使用 Lockdown、Untrusted 完整性级别和 AppContainer。随机调试连接上的标签页操作、示例库存读取和截图通过，退出后旧端口不可访问。

此前的 Windows 运行程序读取了示例订单并截图，来源变化或页面出现密码输入框时拒绝读取。临时桌面窗口通过原生网页来源校验和设备会话握手，使用模拟授权与模拟结果验证操作回执。实际企业账号和同一发行包的完整流程仍需验收。

Windows 10 使用系统 Edge 157.0.4341.46 复测了私有管道和浏览器工作进程。测试在独立配置中打开本机示例订单，读到同源嵌入报表且排除了透明文字，生成截图，并拒绝读取密码页。Windows 进程组回归另验证工作进程异常退出后浏览器子进程能够清理。

麒麟前期核验的 Playwright 路径缺少浏览器二进制，启动失败。所选系统 Chromium 路径仍需在麒麟目标桌面上用同一客户端包断公网复测，记录浏览器软件包来源及版本、启动进程、配置目录、私有调试管道、沙箱和真实页面结果。 [麒麟前期核验](../docs/T01-源码许可与麒麟早期核验.md)
