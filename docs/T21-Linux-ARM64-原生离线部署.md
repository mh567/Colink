# T21 Linux ARM64 原生离线部署

核验日期：2026-11-19。对应 [Issue #22](https://github.com/mh567/Colink/issues/22) 及[父规格 #1](https://github.com/mh567/Colink/issues/1)（用户故事 68、69、72）。

本记录保留银河麒麟桌面操作系统 V10（SP1）ARM64 上的启动诊断和离线运行试验，并补记修复后从本地生成的 ZIP 复验结果。复验使用已有虚拟机和全新数据目录，不等同于从正式发布产物开始的干净系统安装。

---

## 1. 目标环境与交付版本组合

### 目标环境

| 项目 | 目标机实测参数 |
| --- | --- |
| 操作系统发行版 | 银河麒麟桌面操作系统 V10 (SP1) / Kylin Linux Desktop V10 (SP1) |
| 发行版编号 | `KYLIN_RELEASE_ID="2403"`，`PROJECT_CODENAME=V10SP1` |
| 系统内核 | Linux 5.4.18-110-generic #99-KYLINOS SMP aarch64 GNU/Linux |
| 基础 C 库 | glibc 2.31 |
| 系统自带 Python | Python 3.8.10（不满足 `>=3.12`，使用独立绿色打包运行时） |
| 独立运行时 | Python-build-standalone CPython 3.12.12（OpenSSL 3.5.4，SQLite 3.50.4） |
| 包与环境工具 | uv 0.12.22 (aarch64-unknown-linux-gnu) |
| 网络隔离手段 | bubblewrap (`/usr/bin/bwrap --dev-bind / / --unshare-net`) |

### 应用与关键依赖版本

下表为原试验记录的版本。两种架构共用应用版本与依赖锁，但未通过同一数据样例验证两种候选产物的行为一致性：

| 组件 / 依赖 | 采用版本 | 架构与来源 | 角色与验证状态 |
| --- | --- | --- | --- |
| Colink (Octop) | 1.0.3b3 | pure-Python wheel (`py3-none-any`) | 统一主程序，含 Web/API/CLI/服务守护 |
| octop-harness | 1.0.7 | pure-Python wheel (`py3-none-any`) | 智能体执行与工具层 |
| octop-gateway | 1.0.3 | pure-Python wheel (`py3-none-any`) | 消息协议与通道 |
| octop-memory | 1.0.3 | pure-Python wheel (`py3-none-any`) | 记忆摄取与索引 |
| octop-browser | 1.0.0 | pure-Python wheel (`py3-none-any`) | CDP 浏览器管控 |
| cryptography | 49.0.0 | `cp311-abi3-manylinux_2_28_aarch64` | 核心加密库（含 ML-DSA、Ed25519、Fernet） |
| webauthn | 3.0.1 | pure-Python (`py3-none-any`) | 依赖 cryptography 49+ 的 ML-DSA 后量子签名算法 |
| websockets | 15.0.1 | `cp312-cp312-manylinux_2_17_aarch64` | 设备实时加密通信与心跳协议 |
| python-docx | 1.2.0 | pure-Python (`py3-none-any`) | 本地 Word 办公文档生成与校验 |

---

## 2. ARM64 SVE2 指令集冲突诊断与兼容处理

### 根因分析

在 T01 早期实测中，ARM64 环境在导入 `cryptography` 时遭遇致命 `SIGILL`（非法指令信号，退出码 132/exit -4），导致 `init` 与 `start.sh` 均无法执行。

本次通过在客体内使用 GDB 对崩溃点进行了现场捕获和符号分析：

```text
Program received signal SIGILL, Illegal instruction.
0x0000007ff6ce79b8 in _armv8_sve_get_vl_bytes ()
    from .../packages/cryptography/hazmat/bindings/_rust.abi3.so
(gdb) bt
#0  0x0000007ff6ce79b8 in _armv8_sve_get_vl_bytes ()
#1  0x0000007ff68f6508 in OPENSSL_cpuid_setup ()
#2  0x0000007ff7fe1a80 in call_init (l=<optimized out>, argc=argc@entry=1, ...) at dl-init.c:72
...
(gdb) x/i 0x0000007ff6ce79b8
=> 0x7ff6ce79b8 <_armv8_sve_get_vl_bytes>:   cntb    x0
```

- **问题本质**：Apple Silicon / UTM 虚拟化环境下，客体 Linux 内核（5.4.18）在 `/proc/cpuinfo` 中广播了 `Features: ... sve2 ...` 特征，但**没有**包含 `sve` 特征。
- `cryptography 49.0.0` 内部静态链接的 OpenSSL 在执行 CPU 特征初始化（`OPENSSL_cpuid_setup`）时，检测到 sve2 即调用 `_armv8_sve_get_vl_bytes`，执行 SVE 指令 `cntb x0`。由于底层虚拟 CPU 未向客体开放 SVE 向量寄存器/指令执行，处理器立即抛出 `SIGILL`。

### 方案对比与评估

1. **不可行的方案（降级 cryptography 到 46.x）**：
   Colink 的 WebAuthn 身份认证链路采用 `webauthn==3.0.1`，该库导入 `cryptography.hazmat.primitives.asymmetric.mldsa`。原试验将 `cryptography` 降级至 46.x 后缺少该模块，因此保留锁定版本。
2. **兼容处理（仅异常环境回退）**：
   OpenSSL 将 `OPENSSL_armcap` 当作完整能力覆盖值。原试验用的 `0x3f` 强制启用多项能力，不能作为通用默认值。修复后，仅 Linux ARM64 报告 `sve2` 但未报告 `sve` 且用户未显式设置该变量时，使用 `OPENSSL_armcap=0`，关闭 OpenSSL 通过该标志选择的硬件加速路径。正常主机继续自动探测。

### 全链路适配实施

为了使 Linux ARM64 生产部署无需用户手动配置复杂的环境变量，在代码库全链路建立了自适应检测与屏蔽机制：

普通安装由 `octop` 包初始化应用兼容处理；便携包的 `start.sh` 和 `bin/octop` 都经由 `launch.py`，在处理第三方 `.pth` 文件前调用同一检测逻辑。系统服务配置保留显式环境变量；修复后的 systemd 用户服务已在目标机通过托管重启复验。

---

## 3. 原试验：断网环境下运行已组装目录

### 测试方法与网络隔离证明

在目标机上通过 bubblewrap 创建无外部网络的沙箱执行全部测试：
```bash
bwrap --dev-bind / / --unshare-net <command>
```
在沙箱内部，`ip a` 仅有本地回环接口 `lo`，无任何外网网卡与路由，彻底杜绝隐式在线下载或网络依赖。

### 验收用例与执行结果

使用已组装的 `/tmp/colink-t21-portable/Octop-linux-arm64` 和全新数据目录 `/tmp/colink-t21-run` 执行启动及接口试验。以下仅保留原始步骤，不能作为全部验收通过的依据：

```text
[TEST] Portable root: /tmp/colink-t21-portable/Octop-linux-arm64
[TEST] Octop executable: /tmp/colink-t21-portable/Octop-linux-arm64/bin/octop
[TEST] Test home: /tmp/colink-t21-run

=== STEP 1: octop init in offline environment ===
Init stdout:
 ✅ Octop bootstrapped at /tmp/colink-t21-run
   admin user: admin
   next: `octop run` (optional: `octop agent use <id>` to pin default agent)

✓ init completed successfully.
✓ octop.db verified on disk.

=== STEP 2: Start server with octop run ===
✓ Server is listening on 127.0.0.1:9876

=== STEP 3: Admin authentication ===
✓ Admin successfully authenticated, token acquired.

=== STEP 4: Configure Intranet Model Provider ===
✓ Intranet model provider created: id=1
✓ Intranet model provider verified in admin providers list.

=== STEP 5: Create Employee User and Login ===
✓ Employee user employee_arm created.
✓ Employee successfully authenticated.

=== STEP 6: Employee Device Enrollment and Admin Approval ===
✓ Device challenge received.
✓ Device handshake completed: device_id=01M7SCKKC08FDXDHHGNTCDHJKK, status=pending
✓ Admin approved device.
✓ Device heartbeat confirmed: status=approved, session_token acquired.

=== STEP 7: Authorize Local DOCX Office Task ===
✓ Local DOCX create operation authorized successfully.
✓ File event 'completed' logged successfully.

Stopping server for restart test...
✓ Server stopped.

=== STEP 8: Service Restart & Persistence Verification ===
✓ Server restarted and listening on 127.0.0.1:9876
✓ Admin login verified after restart.
✓ Employee login verified after restart.
✓ Intranet model provider persisted: intranet-ollama
✓ Device trust persisted: id=01M7SCKKC08FDXDHHGNTCDHJKK, status=approved, owner=employee_arm

```

---

## 4. 修复后复验

在同一麒麟 ARM64 虚拟机上，用 PR #54 修复后的工作区和仓库打包脚本生成 `Octop-portable-linux-arm64-1.0.3b3.zip`（资源修订版 5）。构建机未装 Clang，使用现有 GCC 编译 `evdev`。ZIP 一致性校验、依赖导入、OCR 打包样例和 PDF 中文样例均通过。

- 断网复验：在只有回环接口启用、无 IPv4/IPv6 路由的 `bwrap` 环境内，将 ZIP 解压到含空格和 `%` 的新目录。未手动指定 OpenSSL 兼容参数；ML-DSA 密钥生成、`bin/octop init`、`start.sh`、健康检查和管理员登录均通过。
- 托管重启：另用同一个 ZIP 和全新数据目录生成临时 systemd 用户服务。重启后进程 PID 已更换，管理员和员工仍可登录，测试模型配置及设备审批状态均保留。此轮未调用真实模型；设备审批由测试程序通过接口完成。

### 交付清单与状态核对

| 需求条目 | 验收结果 | 依据 |
| --- | --- | --- |
| 1. ARM64 运行包与依赖原生安装，断网无 Docker | 部分 | 本地打包 ZIP 在麒麟断网环境通过；正式发布包及干净系统待测 |
| 2. 账号与内网模型配置正常 | 部分 | 登录及配置保存复验通过；未验证真实内网模型调用 |
| 3. 客户端接入与本地任务 | 部分 | 接口层握手、审批、心跳和授权有记录；未验证正式客户端落盘、回读或拒绝用例 |
| 4. 服务重启与数据持久化 | 通过 | systemd 用户服务托管重启后，账号、配置及设备审批状态均保留 |
| 5. 跨架构一致性 | 待测 | 共用应用版本与锁文件；未验证实际产物的数据行为 |

---

## 5. 正式候选包复验

1. 在与目标机兼容的 Linux ARM64 环境运行 `make build-frontend`、`bash desktop/portable/bootstrap-runtime.sh linux-arm64` 和 `bash desktop/portable/package.sh linux-arm64`。需要在断网构建机组包时，先按[便携包离线构建说明](../desktop/portable/README.md#离线包)预取依赖。不要将较新 Ubuntu ARM 构建机的本地 wheel 选择当作麒麟 glibc 2.31 兼容证据。
2. 将实际候选 ZIP 转入干净的麒麟 ARM64 虚拟机，在断网且未安装 Docker 的环境解压。记录候选包名称、来源提交、系统与实际 wheel 版本。使用含空格的安装目录验证 `bin/octop init --yes`、服务安装、启动和 `systemctl restart octop`；确认账号、模型配置和设备信任状态保留。
3. 保持公网断开，仅接通隔离测试内网。用正式客户端完成登录、批准设备和模型调用；在授权目录生成 DOCX，回读文件并核对内容。另以只读或撤销授权的目录重试，确认请求被拒绝、无新文件。以同版本 x86_64 候选包复跑同一数据样例，记录结果差异。

## 6. 局限

1. **办公能力**：OCR 和 PDF 打包样例通过，不代表正式客户端的 OCR、XLSX 或 PDF 端到端通过。
2. **浏览器无头环境**：断网环境下若需要调用基于 CDP 的浏览器自动化，需提前在离线资源包中放入 ARM64 架构匹配的 Chromium / headless-shell 二进制及系统字体。
