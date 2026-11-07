# Octop 绿色便携包（多平台）

解压即用：内置便携 CPython + Octop 及依赖，通过 `start.sh` / `start.bat` 启动。  
**不依赖**系统 Python，也**不包含** Wails / 桌面壳——用浏览器打开 Dashboard。  
首启走上游正常 setup wizard（本目录**不含** OOB / UI 裁剪）。

## 与上游解耦

本能力全部落在：

- `desktop/portable/**`（脚本 / 模板 / 本目录 Makefile）
- `.github/workflows/octop-desktop.yml`（多平台 CI）
- `desktop/portable/.gitignore`（`/release/` `/runtimes/` `/wheels/` 等忽略构建产物）
- `tests/unit/test_green_launch.py`（launch.py PATH / addsitedir）

便携包依赖沿用根目录 `pyproject.toml` 与 `uv.lock`；本机 OCR 使用 `local-ocr` extra。
合并上游时只需留意上述路径；日常用：

```bash
make -f desktop/portable/Makefile green
```

依赖版本必须以仓库根目录 `uv.lock` 为准：`package.sh` 使用
`uv export --frozen`，出包后跑 `desktop/portable/verify_imports.py`
校验关键包 pin 与 import（含 `langchain-openai` / `langchain-core` 配对），
避免「同安装、不同环境」因版本错配或原生扩展加载失败而偶发报错。
平台 overrides（`darwin-amd64` / `windows-arm64` 的 `cryptography==46.x`）
会一并传入校验，避免与 lock 中的 49.x 误报不一致。

## 产物布局

公开文件名：`Octop-portable-<plat>-<version>.zip`。zip 内目录仍是：

```
Octop-<plat>/
  runtime/       # python-build-standalone
  packages/      # Octop + 依赖（site-packages，可搬迁）
  launch.py      # 启动引导（site.addsitedir / Windows pywin32）
  start.sh       # macOS / Linux
  start.bat      # Windows
  README.txt
  data/          # 首次运行自动创建（用户数据）
```

支持平台：`darwin-arm64` `darwin-amd64` `linux-amd64` `linux-arm64` `windows-amd64` `windows-arm64`。

## 构建（在仓库根目录）

需已安装：`uv`、`curl`、`zip`（可选）、Node（编前端）。

```bash
# 一键：当前主机平台（前端 + 便携 CPython + zip）
make -f desktop/portable/Makefile green

# 或分步：
make build-frontend                  # 上游已有目标
bash desktop/portable/bootstrap-runtime.sh
bash desktop/portable/package.sh
```

本地一键重建（nvm 24）：

```bash
bash desktop/portable/rebuild.sh
```

交叉组装其它平台时，**带 C 扩展的包**必须在目标 ABI 上构建：

| 目标 | 推荐方式 |
|------|----------|
| 当前主机 | `make -f desktop/portable/Makefile green` |
| Linux（从 macOS/Windows） | `make -f desktop/portable/Makefile green-linux` |
| Windows | 在 Windows / CI 上执行同上 `green` |

### 离线包

```bash
bash desktop/portable/vendor-wheels.sh          # 在目标平台按当前 uv.lock 构建 wheel 缓存
OCTOP_GREEN_OFFLINE=1 bash desktop/portable/package.sh
```

离线缓存必须来自**当前分支**的 `uv.lock`，不要复用旧分叉的 wheel 目录。

`vendor-wheels.sh` 在联网构建机使用隔离的 pip 25.3 构建工具生成完整 wheel 缓存；pip 不加入产品运行依赖。冻结导出先应用既有平台覆盖，再作为 pip 的精确版本约束，`--no-deps` 禁止另行解析和升级产品依赖。只有源码包的依赖（如 `antlr4-python3-runtime==4.9.3`）会在此时构建 wheel，断网安装不再临时下载构建工具。

缓存必须在目标操作系统和架构上生成，包括未启用 OCR 的平台；脚本明确拒绝跨平台预取，以免按构建机条件漏掉目标平台依赖。非 OCR 平台的 `package.sh` 仍保留原有跨平台、仅二进制组装方式，可使用对应原生 runner 生成的缓存。OCR 平台的组包和样本验证均须原生执行。

### macOS Intel（`darwin-amd64`）注意

锁定的 `cryptography` 49.x **不再发布** macOS x86_64 / universal2 wheel。若允许从 sdist 编译，会链到构建机 Homebrew 的 `/usr/local/opt/openssl@3`，用户机缺库即启动失败。

绿包脚本已做：

1. `darwin-amd64` 覆盖钉死 `cryptography==46.0.3`（仍有 `macosx_*_universal2` wheel）
2. 全平台 `--only-binary cryptography`，禁止源码编译
3. 打包后 `otool` 检查，拒绝 Homebrew/MacPorts 绝对路径
4. smoke import 覆盖 `cryptography.fernet`

### Windows / pywin32

`mcp` / `docker` 在 win32 上传递依赖 `pywin32`。打包脚本会：

1. 若 `packages/pywin32_system32` 缺失则显式 `uv pip install pywin32`
2. 把 `pywintypes*.dll` / `pythoncom*.dll` 拷到 `runtime/`
3. `launch.py` 用 `site.addsitedir` 处理 `.pth`，并 `os.add_dll_directory`

**不要**设置 `PYTHONPATH=packages`（会跳过 `.pth`，导致 `No module named pywintypes`）。

`windows-arm64` 另排除无 wheel 的 `psycopg-binary` / `sqlite-vec`，并把 `cryptography` 钉到 `46.0.0`（仅该版本提供 `win_arm64` wheel）。

## CI

[`.github/workflows/octop-desktop.yml`](../../.github/workflows/octop-desktop.yml) 在 6 个 runner 上出 zip：

`linux-amd64` `linux-arm64` `darwin-arm64` `darwin-amd64` `windows-amd64` `windows-arm64`

Actions 使用 GitHub 上游 PBS（`PBS_BASE_URL`），本机构建默认 npmmirror。产物以 `archive: false` 上传，避免 zip-in-zip。

## Electron 壳

壳只消费 `Octop-portable-<plat>-<version>.zip`（zip 内仍是 `Octop-<plat>/`），不要把绿包编进 asar。步骤见
[`AGENT_ELECTRON_INTEGRATION.md`](AGENT_ELECTRON_INTEGRATION.md)。

## 本机扫描 PDF 识别资源

`linux-amd64`、`linux-arm64`、`windows-amd64`、`darwin-arm64` 便携包包含 `local-ocr` extra：RapidOCR 3.9.2、ONNX Runtime 1.28.0、pypdfium2 5.15.0，具体传递依赖由 `uv.lock` 锁定。`vendor-wheels.sh` 同步缓存这些 wheel；三个识别模型已包含在 RapidOCR wheel，组包时复制到 `ocr/models/`，运行时不下载模型或字体。Python 组件及 PDFium 的许可留在 `packages/`，补充的 RapidOCR/PaddleOCR 许可随 `ocr/licenses/` 交付。

构建在目标平台原生运行 `desktop/ocr/prepare.py`，使用包内解释器识别固定两页中文样本并核对关键文字，通过后才压缩。交叉组装不能替代目标平台执行，因此包含 OCR 的目标明确拒绝跨平台组包；请使用对应原生 runner 或现有 Linux Docker 构建入口。运行时禁止 Python 网络连接，PDF 和识别文字仅通过本机子进程管道传输。

本流程的文字结果不保留排版。限制为 20 MB、20 页、每页渲染不超过 1600 万像素和 60000 字符；空页、损坏、加密、超限或缺资源均明确失败，不静默截断。

本机 OCR 已在 macOS ARM64 和麒麟 ARM64 的临时便携资源目录验证，这不等于完整 Wails 安装包验收。锁定 ONNX Runtime wheel 要求 macOS ARM64 14+，且不提供 macOS Intel wheel。`darwin-amd64`、`windows-arm64` 保留原有无 OCR 打包流程，扫描 PDF 任务明确提示不支持。Windows x86_64、Linux x86_64 仍缺少目标系统断公网实测证据。

Linux ARM64 的 `local-ocr` 使用官方 OpenCV 4.11.0.86 wheel，其他平台保留 5.0.0.93。此兼容约束用于麒麟旧内核可能报告 SVE2、却未报告 SVE 的情况；NumPy 保持 2.5.1。后续调整此版本必须重新执行目标机断网样本验证，不能用新版可安装代替可运行。

2026-11-07 麒麟实测：UTM 中的 V10 SP1/2403、ARM64、内核 5.4.18-110、glibc 2.31、CPython 3.12.12。原 OpenCV 5.0.0.93（两种 manylinux wheel）及 4.14.0.94 在加载时触发 SIGILL；实际出错的是内嵌 KleidiCV 的 `cntb x0`。其 [26.03 官方分发逻辑](https://gitlab.arm.com/kleidi/kleidicv/-/blob/26.03/kleidicv/include/kleidicv/dispatch.h)仅检查 `HWCAP2_SVE2` 后便探测 SVE 向量长度；`OPENCV_CPU_DISABLE` 不控制这段初始化。4.12.0.88 能识别样本，但其 NumPy 上限与完整客户端依赖冲突，因此未采用。4.11.0.86 的 Linux ARM64 wheel 不含该 KleidiCV 路径，既能识别，也能通过完整依赖锁定。

按更新后的 `uv.lock` 在全新目录安装识别组件，由正式 `prepare.py` 复制模型并验证，再在 `bwrap --unshare-net` 中运行正式 worker：11 项 OCR 测试通过，损坏、加密、空页、超限、缺资源及坏模型均明确失败；固定两页样本成功输出 TXT，标题、单号、金额、负责人、完整日期和两个页码共 7 项核对通过。断网探测返回 `ENETUNREACH`；测试前后 CPU 能力仍为 `AT_HWCAP=0x2fb3ffff`、`AT_HWCAP2=0x183`。本次只调整客户端依赖，没有升级内核或修改虚拟机配置，也不改写系统 CPU 能力报告。

macOS ARM64 的原生任务测试另确认 TXT 落盘、只发送授权动作、防覆盖、撤销写入后拒绝产物及重启状态保留。浏览器表单与错误提示使用模拟桌面事件核查，完整 Wails 安装包联调及其他目标系统验收仍须分别完成。
