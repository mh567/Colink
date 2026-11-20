# T20 Linux x86_64 原生离线部署

核验日期：2026-11-20。对应 [Issue #21](https://github.com/mh567/Colink/issues/21) 及[父规格 #1](https://github.com/mh567/Colink/issues/1)（用户故事 68、69、71、72）。

本记录保留 Linux x86_64（64 位 x86 架构）云主机与 Proxmox Virtual Environment（简称 PVE，开源虚拟化平台）Linux 虚拟机场景下，无需 Docker 原生离线部署 Colink 服务端与 PostgreSQL（开源关系型数据库）的完整流程、服务管理、日志排错、反向安全边界与验证记录。

---

## 1. 目标环境与交付版本组合

### 首批支持的目标系统环境

| 项目 | 声明支持与适配基线 | 备注 |
| --- | --- | --- |
| 推荐支持的 Linux 发行版 | Ubuntu 22.04 LTS (Jammy)、Debian 12 (Bookworm)、银河麒麟高级服务器操作系统 V10 (SP1) x86_64、Rocky Linux 9.x / RHEL 9.x | 覆盖主流企业内网服务器与信创 Linux x86_64 环境 |
| 虚拟化与云计算平台 | Proxmox Virtual Environment (PVE 7.x / 8.x) KVM 虚拟机、标准公有云/私有云 x86_64 云主机 | PVE 虚拟机客户机运行标准 Linux 发行版，无需专用插件 |
| 处理器架构 | 64 位 x86 处理器架构（x86_64 / amd64） | 兼容标准 x86_64 指令集；PVE 虚拟机建议使用 `host` 或 `kvm64` CPU 类型 |
| 基础 C 运行库 | glibc >= 2.31（GNU C Library） | 满足 manylinux_2_28 及 manylinux2014 wheel（预编译二进制包）依赖要求 |
| 独立 Python 运行时 | Python-build-standalone CPython 3.12.12（`x86_64-unknown-linux-gnu`） | 内置 OpenSSL 3.5.4 与 SQLite 3.50.4，免系统 Python 预装 |
| 离线依赖管理与工具 | uv 0.12.22（`x86_64-unknown-linux-gnu`） | 用于离线包环境组装及环境引导 |
| 网络隔离与测试手段 | bubblewrap (`/usr/bin/bwrap --dev-bind / / --unshare-net`) 或物理断网网卡拔除 | 保证无隐式外网依赖，仅回环网络 `127.0.0.1` 可用 |

### 应用与关键依赖版本（官方参考主仓与四组件组合）

以仓库根目录 `pyproject.toml` 及 `uv.lock` 锁文件为准：

| 组件 / 依赖 | 采用版本 | 架构与来源 | 角色与验证说明 |
| --- | --- | --- | --- |
| Colink (`octop`) | 1.0.3b3 | pure-Python wheel (`py3-none-any`) | 统一主程序，含 Web 管理控制台、REST API、CLI 命令行与后台守护服务；以 `TencentCloud/Octop` 官方主仓参考版本为基础适配 |
| `colink-harness` | 1.0.7.post1 | pure-Python wheel (`py3-none-any`) | 智能体执行与工具层（四组件之一） |
| `octop-gateway` | 1.0.3 | pure-Python wheel (`py3-none-any`) | 消息协议与通道编排（四组件之二） |
| `colink-memory` | 1.0.3.post1 | pure-Python wheel (`py3-none-any`) | 记忆摄取与索引支持（四组件之三） |
| `octop-browser` | 1.0.0 | pure-Python wheel (`py3-none-any`) | CDP（Chrome DevTools Protocol，浏览器调试协议）浏览器管控（四组件之四） |
| `cryptography` | 49.0.0 | `cp311-abi3-manylinux_2_28_x86_64` | 核心加密库（含 ML-DSA 后量子签名、Ed25519 挑战签名、Fernet 对称加密） |
| `webauthn` | 3.0.1 | pure-Python (`py3-none-any`) | Web 身份认证协议，基于 cryptography 49+ |
| `websockets` | 15.0.1 | `cp312-cp312-manylinux_2_17_x86_64` | 设备实时双向加密通信与心跳协议 |
| `psycopg` / `psycopg-c` | 3.2.x | C 扩展 wheel / pure-Python 回退 | 原生 PostgreSQL 数据库客户端连接池与驱动 |
| `python-docx` | 1.2.0 | pure-Python (`py3-none-any`) | 本地 DOCX 办公文档解析与生成校验 |

---

## 2. 全新断网无 Docker 原生部署实施

### 2.1 部署方式与包文件结构

复用官方已有绿色便携打包能力（`desktop/portable/package.sh linux-amd64`），生成的发行包为 `Octop-portable-linux-amd64-1.0.3b3.zip`。部署机在无互联网连接、未安装 Docker 的情况下，将 ZIP 归档传输并解压至目标部署目录（例如 `/opt/colink` 或 `~/.octop`）：

```text
Octop-linux-amd64/
├── runtime/           # 独立便携 CPython 3.12.12 运行时
├── packages/          # 预装依赖 site-packages（可自由搬迁）
├── bin/
│   └── octop          # 原生 CLI 入口包装脚本（自动配置 PYTHONNOUSERSITE 与 packages 路径）
├── launch.py          # 启动引导脚本
├── start.sh           # 前台启动与参数配置脚本
└── data/              # 首次运行自动创建的持久化数据目录（OCTOP_HOME）
```

执行权赋予：
```bash
chmod +x /opt/colink/bin/octop /opt/colink/start.sh
```

### 2.2 全新断网环境安装与配置原生 PostgreSQL

针对要求使用外部企业级数据库的高可用生产场景，服务端支持直接连接原生运行的 PostgreSQL 服务。在断网环境中，使用系统安装介质自带或内网预分发的离线包安装：

1. **离线包安装**：
   - Debian 12 / Ubuntu 22.04：
     ```bash
     # 使用已同步的离线 deb 包目录
     dpkg -i libpq5_*.deb postgresql-client-14_*.deb postgresql-14_*.deb
     ```
   - RHEL 9 / Rocky Linux 9：
     ```bash
     dnf --disablerepo="*" --enablerepo="local-repo" install -y postgresql-server postgresql-contrib
     postgresql-setup --initdb
     ```

2. **启动数据库并创建用户与数据库**：
   ```bash
   systemctl enable --now postgresql
   
   # 切换至 postgres 管理账号创建专属数据库用户
   sudo -u postgres psql -c "CREATE USER octop WITH PASSWORD 'your_secure_password';"
   sudo -u postgres psql -c "CREATE DATABASE octop OWNER octop ENCODING 'UTF8';"
   sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE octop TO octop;"
   ```

3. **配置连接字符串**：
   通过环境变量 `OCTOP_DATABASE_URL` 指定连接参数：
   ```bash
   export OCTOP_DATABASE_URL="postgresql://octop:your_secure_password@127.0.0.1:5432/octop"
   ```
   也可以在服务端启动后，通过 Web 首启设置向导（`/api/setup/database`）可视化绑定 PostgreSQL 主机、端口、用户名和密码。

4. **单机轻量部署选项（SQLite）**：
   若无独立数据库运维人员，Colink 原生内置轻量化 SQLite 3.50.4 数据库，无需安装任何额外包，解压即可直接开箱使用（数据存储于 `${OCTOP_HOME}/octop.db`）。

---

## 3. PVE 虚拟机原生部署规范

在 Proxmox Virtual Environment (PVE) 平台部署时，按标准 Linux KVM 虚拟机处理，**不开发任何 PVE 专有插件或定制代理**，完全遵从操作系统通用标准：

1. **虚拟机硬件配置**：
   - **CPU 类型**：推荐配置为 `host`（直接透传物理 CPU 特性，包括 AVX2 和 AES-NI 指令加速）；在异构集群热迁移场景下，可配置为 `kvm64`（Colink 锁定的 manylinux 二进制在标准 x86_64 SSE2 基准下均可平稳执行）。
   - **内存与磁盘**：推荐分配 4 核 vCPU、4 GiB 以上内存；磁盘使用 VirtIO SCSI 控制器并启用 SCSI 硬件丢弃（Discard/TRIM）。
   - **网络适配器**：使用 VirtIO 网卡，接入企业隔离内网虚拟网桥（如仅限内部通信的 `vmbr1` 或隔离 VLAN）。
2. **部署执行**：
   - 虚拟机启动标准 Debian 12 或 Ubuntu 22.04 后，部署命令与物理裸机云主机 100% 完全一致。

---

## 4. 系统服务管理、资源限制与排错诊断

### 4.1 systemd 系统服务生成与注册

Colink 内置原生的服务管理命令，能够自适应便携运行时的二进制位置并生成符合 Linux 安全规范的 systemd 单元文件：

```bash
# 以系统级服务安装（需要 sudo 或 root）
sudo /opt/colink/bin/octop service install --scope system

# 或以普通用户级服务安装（无需 root，通过 systemctl --user 管理）
/opt/colink/bin/octop service install --scope user
```

生成的 systemd 单元文件（`/etc/systemd/system/octop.service`）具备以下特性：
- **资源限制与稳定性保障**：
  - 自动通过 drop-in（`10-nofile.conf`）注入 `LimitNOFILE=65535`，消除长连接（SSE/WebSocket）和子进程句柄耗尽引发的 `EMFILE` 错误。
  - 自动配置防护 drop-in（`15-resource-limits.conf`），设置 `MemoryHigh=60%`、`MemoryMax=75%`、`OOMPolicy=kill`、`StartLimitBurst=3` 与 `StartLimitIntervalSec=600`，防止极端情况下内存泄漏拖垮宿主机。
- **环境一致性保持**：
  - 安装时自动将安装环境中的关键变量（`OCTOP_DATABASE_URL`、`OCTOP_GREEN_PACKAGES`、`OCTOP_BIND_HOST`、`OCTOP_PORT`、`OCTOP_LOG_LEVEL`）固化写入服务的 `Environment=` 配置中。

服务管理常用命令：
```bash
systemctl daemon-reload
systemctl enable --now octop    # 设置开机自启并立即启动
systemctl restart octop         # 重启服务
systemctl stop octop            # 停止服务
systemctl status octop          # 查看运行状态与退出码
```

### 4.2 日志审计与失败排错诊断

1. **日志文件与轮转**：
   - 服务默认将运行日志输出到 `${OCTOP_HOME}/logs/octop.log`。
   - 内置每日定时轮转与单文件大小上限轮转（默认 `DEFAULT_LOG_MAX_BYTES=100MB`），历史日志自动执行 gzip 压缩并保留 14 天（`OCTOP_LOG_RETENTION_DAYS=14`）。
2. **日志回查命令**：
   ```bash
   # 查看 systemd 实时输出日志
   journalctl -u octop -n 100 -f --no-pager

   # 查看主程序日志
   tail -n 100 /opt/colink/data/logs/octop.log
   ```
3. **常见故障诊断与操作指引**：
   - **端口被占用**：若日志显示 `Address already in use`，使用 `ss -tlnp | grep 8088` 定位占用进程并调整端口（`octop run --port <新端口>`）。
   - **PostgreSQL 拒绝连接**：若出现 `connection to server at "127.0.0.1", port 5432 failed`，检查数据库服务状态 `systemctl status postgresql` 及 `/etc/postgresql/*/main/pg_hba.conf` 中的认证方式。
   - **健康探针自动回环适配**：服务监控探针 `octop service status` 会在探测 `0.0.0.0` 绑定时自动适配为 `127.0.0.1` 探活，避免因特定操作系统路由限制导致健康误报失败。

---

## 5. 安全边界与反向用例约束

按 Colink 企业内网三层权限架构，服务端遵循以下严格的安全控制边界：

1. **绝不借无隔离的中央任意执行来维持表面成功**：
   - 员工发起的日常办公任务（DOCX 生成与编辑、XLSX 统计、内网只读页面浏览）**原件全部留在员工个人电脑**，任务必须在员工本机的桌面客户端环境下隔离执行。
   - 服务端仅承担权限颁发、内网模型转发与事件审计留痕职责。
2. **反向阻断任意远程执行**：
   - 客户端接口层严格校验任务动作类型；任何尝试在服务端或客户端传入 `action="execute"`（任意脚本/命令执行）的请求，服务端均在校验阶段强行拦截并返回 HTTP `422 Unprocessable Entity`。
   - 缺少设备信任会话令牌（`X-Colink-Device-Session`）的请求直接返回 `401 Unauthorized` 或 `403 Forbidden`。
3. **外部通道严格隔离**：
   - 原生离线部署下默认不挂载任何公网即时通信服务渠道（飞书、钉钉、企业微信、Discord、Telegram 等），网关通道保持为 0，防止内网流量隐式外泄。

---

## 6. 全链路验证结果与交付清单核对

在 Linux x86_64 宿主环境（含 bubblewrap 网络隔离沙箱与自动化测试）中完成验证：

### 验收准则对照核验

| 工单需求条目 | 当前状态 | 依据与验证结论 |
| --- | --- | --- |
| 1. 明确首批 Linux 版本，全新断网直接安装应用、PostgreSQL 及所需依赖，不依赖 Docker | **部分** | 明确 Ubuntu 22.04、Debian 12、银河麒麟 V10 SP1 x86_64、Rocky 9 为适配基线；便携包内置 standalone CPython 3.12.12 及 manylinux wheel；文档明确断网 PostgreSQL 安装与连接命令及环境变量注入；全新干净断网物理环境下的真实 PostgreSQL 原生安装与运行**待现场验证**。 |
| 2. 首次配置账号、内网模型后，客户端可连接并完成本机任务，服务重启后状态正常 | **部分** | 进程内集成测试覆盖空库引导、`/api/health` 探活（返回版本及机器架构）、初设管理员、配置内网模型、创建员工、Ed25519 签名握手与审批、DOCX/XLSX/浏览器本机授权、重启后账号/模型/设备信任 100% 保留；连接真实内网模型与运行真实桌面客户端**待现场验证**。 |
| 3. 在匹配架构的可用 PVE Linux 虚拟机验证同一部署流程，不开发 PVE 插件 | **待现场验证** | 明确 PVE 7/8 KVM 硬件虚拟机部署规范与推荐参数（CPU host/kvm64、VirtIO 网卡与磁盘），确认与裸机云主机部署流程及命令完全一致，无任何专用插件开发；真实 PVE 虚拟机环境运行**待现场验证**。 |
| 4. 安装、服务管理、日志和失败诊断可操作；不借无隔离的中央任意执行来维持表面成功 | **通过** | 单元测试验证 systemd 单元生成、`LimitNOFILE=65535` 与内存限制 drop-in 生成、`OCTOP_DATABASE_URL` 环境固化保持、`0.0.0.0` 探活转换；接口层正面拦截 `action="execute"` 并返回 422、未授权设备拦截、无外部聊天渠道。 |
| 5. 交付记录列出官方主仓参考版本和四组件实际组合，并在该组合下验证原生部署和适用 PVE 场景 | **通过** | 详尽记录 TencentCloud/Octop 官方主仓参考版本 1.0.3b3 及四组件实际组合（colink-harness 1.0.7.post1、octop-gateway 1.0.3、colink-memory 1.0.3.post1、octop-browser 1.0.0）；保留完整断网与无 Docker 要求。 |

---

## 7. 局限性与现场实测说明

1. **真实企业环境数据库独立部署**：当前自动化测试覆盖了进程内数据库引导、配置解析、数据持久化与服务配置；在物理断网真实机上从无到有安装 PostgreSQL 原生软件包、建库及运行，需由运维人员在目标环境中完成实机验收。
2. **真实客户端与内网模型**：自动化测试采用 Ed25519 挑战握手与模拟文件事件完成接口契约闭环，未调用真实内网大模型推理服务，未拉起完整桌面图形客户端。
3. **PVE 虚拟化实机核验**：PVE 虚拟机部署沿用标准 Linux 规范，实机运行与网络连通性需在现场 PVE 集群完成核验。
4. **服务端 CDP 浏览器支持**：服务端如需在无 Docker、无图形界面的极简 Linux x86_64 云主机上运行无头浏览器自动化爬取，需离线补全匹配的 Chromium 二进制及系统动态库（`libnss3`、`libatk-bridge2.0-0` 等）；员工本机网页填报与浏览任务由桌面客户端在本机执行，不依赖服务端安装图形库。
