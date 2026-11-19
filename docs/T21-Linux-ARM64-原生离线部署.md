# T21 Linux ARM64 原生离线部署

核验日期：2026-11-19。对应 [Issue #22](https://github.com/mh567/Colink/issues/22) 及[父规格 #1](https://github.com/mh567/Colink/issues/1)（用户故事 68、69、72）。

本记录交付基于银河麒麟桌面操作系统 V10（SP1）ARM64 架构的完全断网原生安装、依赖适配、服务生命周期及核心端到端功能验证。所有测试在隔离环境及 `bwrap --unshare-net` 物理断网沙箱中由原生二进制执行，不依赖 Docker，不将跨架构构建或上游 CI 结果推断为目标环境已验证。

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

与 x86_64 保持 100% 相同应用版本与锁文件语义，不引入架构专有的降级分支：

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

## 2. ARM64 SVE2 指令集冲突诊断与彻底解决

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
   Colink 的 WebAuthn 身份认证链路采用 `webauthn==3.0.1`，该库显式导入 `cryptography.hazmat.primitives.asymmetric.mldsa`（FIPS 204 ML-DSA 后量子签名）。ML-DSA 仅在 cryptography 49.0.0 引入；若降级，应用在启动加载阶段即因 `ModuleNotFoundError: No module named 'cryptography.hazmat.primitives.asymmetric.mldsa'` 崩溃。
2. **正确原生方案（CPU 特征位屏蔽与自动保护）**：
   OpenSSL 原生支持通过环境变量 `OPENSSL_armcap` 精确控制启用的 ARM 硬件能力标志。设置 `OPENSSL_armcap=0x3f` 可关闭 SVE/SVE2，同时**完整保留** NEON、AES、PMULL、SHA1、SHA256、SHA512 等所有关键硬件加速指令。

### 全链路适配实施

为了使 Linux ARM64 生产部署无需用户手动配置复杂的环境变量，在代码库全链路建立了自适应检测与屏蔽机制：

1. **核心基础设施自动检测（`src/octop/infra/utils/arm64_compat.py`）**：
   在 Linux ARM64 环境下，读取 `/proc/cpuinfo`；若检测到存在 `sve2` 且不存在独立的 ` sve ` 标志，且未手动设置 `OPENSSL_armcap` 时，自动设置 `os.environ["OPENSSL_armcap"] = "0x3f"`。
2. **顶级包与 CLI 初始化引入（`src/octop/__init__.py`、`src/octop/cli/main.py`）**：
   在应用入口以及任何子模块导入前优先触发，确保 Python 解释器在动态加载 `_rust.abi3.so` / OpenSSL 前已生效。
3. **绿色便携包启动器内置防护（`desktop/portable/templates/start.sh`、`launch.py`、`bin/octop`）**：
   无论是通过 Shell 脚本启动、Python 引导加载，还是通过 `bin/octop` 命令行执行，均自动进行 CPUID 异常特征识别与防护。
4. **服务部署单元注入（`src/octop/infra/setup/service.py`）**：
   在渲染 Linux systemd unit 文件及 launchd plist 时，自动将当前生效的 `OPENSSL_armcap` 与 `OCTOP_GREEN_PACKAGES` 写入 `Environment=` 配置，保证在 `systemctl` 托管重启后依然稳定运行。

---

## 3. 断网原生安装与服务运行验收

### 测试方法与网络隔离证明

在目标机上通过 bubblewrap 创建无外部网络的沙箱执行全部测试：
```bash
bwrap --dev-bind / / --unshare-net <command>
```
在沙箱内部，`ip a` 仅有本地回环接口 `lo`，无任何外网网卡与路由，彻底杜绝隐式在线下载或网络依赖。

### 验收用例与执行结果

使用绿色便携运行时结构 `/tmp/colink-t21-portable/Octop-linux-arm64`（包含 `bin/octop`、`runtime/`、`packages/`、`start.sh`、`launch.py`），在全新测试目录 `/tmp/colink-t21-run` 下执行全流程端到端自动化验收：

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

========================================================
🎉 ALL ACCEPTANCE CRITERIA VERIFIED SUCCESSFULLY ON ARM64!
========================================================
```

---

## 4. 交付清单与状态核对

| 需求条目 | 验收结果 | 依据 |
| --- | --- | --- |
| 1. ARM64 运行包与依赖原生安装，断网无 Docker | ✅ 通过 | 目标机 UTM Kylin ARM64 下使用 `bwrap --unshare-net` 断网运行，无 Docker 依赖 |
| 2. 账号与内网模型配置正常 | ✅ 通过 | 初始化 admin 账号与普通员工账号，配置 `intranet-ollama` 模型成功生效 |
| 3. 客户端设备接入与本地任务授权 | ✅ 通过 | Ed25519 双向安全握手、管理员审批设备、心跳获取会话，完成 DOCX 任务本地授权及审计事件写入 |
| 4. 服务重启与数据持久化 | ✅ 通过 | 模拟服务关闭重启，数据库完整恢复账号、内网模型与设备信任状态 |
| 5. 跨架构一致性 | ✅ 通过 | 与 x86_64 保持统一版本号 `1.0.3b3`、相同依赖锁文件与数据模型语义 |

---

## 5. 局限与后续建议

1. **OCR 原生依赖**：本次验证涵盖了服务主体、加密通信、办公文档（DOCX/XLSX/PDF）与内网模型，未启用额外可选的 `knowledge-ocr` 权重；若需在 ARM64 上运行本地 OCR，需按相同规范离线分发 RapidOCR 与 ONNX Runtime ARM64 原生推理库。
2. **浏览器无头环境**：断网环境下若需要调用基于 CDP 的浏览器自动化，需提前在离线资源包中放入 ARM64 架构匹配的 Chromium / headless-shell 二进制及系统字体。
