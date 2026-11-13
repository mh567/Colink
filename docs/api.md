# API Reference

Every route is mounted under `/api`. JSON unless otherwise noted; SSE
endpoints emit `text/event-stream`; chat turns use a WebSocket
described in [Chat (WebSocket)](#chat-websocket).

> **Interactive docs** (Scalar) live at `/api/docs` when
> `enable_api_docs=true` (or `OCTOP_ENABLE_API_DOCS=1`). The schema
> is at `/api/openapi.json`.

## Authentication

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <access_token>` from `POST /api/auth/login` |

Tokens expire after `OCTOP_ACCESS_TOKEN_TTL` seconds (default 24 h).
An authenticated request renews the token once less than two-thirds of that
lifetime remains, so active use extends the session from the last renewal.
Changing the password invalidates outstanding tokens; the change-password
response returns a replacement in `X-Octop-Access-Token`.
Rotating the JWT secret (`octop admin rotate-jwt-secret`) invalidates
every outstanding token immediately. Login attempts are rate-limited
(`OCTOP_LOGIN_MAX_ATTEMPTS` / `OCTOP_LOGIN_LOCKOUT_SECONDS`); the
admin can clear the lockout with `POST /api/users/{id}/unlock-login`.

### Auth column legend

- **public** — no token required.
- **user** — any logged-in account.
- **owner** — same user that owns the resource (or admin).
- **admin** — admin role required.

### Public endpoints (no token)

`/api/docs`, `/api/openapi.json`, `/api/health`, `/api/setup/*`,
`/api/auth/login`, `/api/auth/captcha`, `/api/auth/oidc/status`, `/api/auth/oidc/start`,
`/api/auth/oidc/callback`, `/api/auth/oidc/exchange`, `/api/auth/oauth/status`,
`/api/auth/oauth/start`, `/api/auth/oauth/callback`, `/api/auth/oauth/exchange`,
`/api/auth/ldap/status`, `/api/auth/passkey/status`,
`/api/connectors/oauth/callback`, and `/api/internal/mcp/*`. All other routes are JWT-gated by
`api/middleware/jwt_auth.py`; the setup lockdown middleware
(`api/middleware/setup_lockdown.py`) additionally blocks non-setup
routes until the wizard finishes.

Password login may require a vendor captcha token. `GET /api/auth/captcha`
returns `{provider: "slider"}` by default (dashboard slider only; no server
check). When a strong provider is active (`turnstile`, `hcaptcha`,
`recaptcha`, `recaptcha-v3`, `tencent`), `POST /api/auth/login` must include
`captcha_token` (max 4096 characters; for `tencent` the dashboard sends the
callback pair as `ticket:randstr`, verified via GET with the client IP).
Captcha failures do not increment
login lockout. OIDC login is unchanged. A request for an unknown username
with a garbage token still triggers one outbound siteverify request (bounded
by a 10s timeout and the token length limit); a known locked user is
rejected before siteverify.

Offline recovery: delete the `captcha.settings` row via local CLI /
`settings_repo` if a settings-sourced strong provider is unreachable. If
the process refused to start because `OCTOP_CAPTCHA_PROVIDER` is strong
without both keys and no readable settings blob, edit `~/.octop/env` on
disk — the dashboard and `PUT /api/envs` are unreachable until the
process starts. `GET /api/envs` redacts `OCTOP_CAPTCHA_SECRET` as
`********`; sending that sentinel on PUT keeps the on-disk value.

If you serve the dashboard behind a CSP, allow
`challenges.cloudflare.com`; `js.hcaptcha.com`, `newassets.hcaptcha.com`,
`api.hcaptcha.com`; `www.google.com`, `www.gstatic.com`;
`turing.captcha.qcloud.com`, `captcha.qq.com`, `ssl.captcha.qq.com`. Octop
does not set these headers itself.

## Setup & auth

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/setup/status` | public | `{required, completed, has_admin}` |
| `GET`    | `/setup/presets` | public | Provider templates for the wizard |
| `POST`   | `/setup/begin` | public | Begin a wizard session (no password) |
| `POST`   | `/setup/verify-password` | public | Exchange the wizard password for a session token |
| `GET`    | `/setup/validate-token` | public | Check a wizard session token |
| `POST`   | `/setup/initial-admin` | public | body `{username, password, display_name?, email?}` → `201` |
| `POST`   | `/setup/test-provider` | public | Ping a provider draft (kind/base_url/api_key/model) |
| `POST`   | `/setup/finish` | public | Finalise setup and unlock the rest of the API |
| `GET`    | `/auth/captcha` | public | `{provider, site_key?}` for the login widget; 503 while setup is required |
| `POST`   | `/auth/login` | public | body `{username, password, captcha_token?}` (`username` may be email) → `{access_token, role, user, ...}`. When no local password matches, the credentials are retried against the configured LDAP directory; a first directory login provisions an Octop account (unless `auto_provision` is off) and derives the role from `admin_groups` |
| `GET`    | `/auth/oidc/status` | public | OIDC login availability and provider display name |
| `POST`   | `/auth/oidc/start` | public | body `{redirect_after?}` → identity-provider authorization URL |
| `GET`    | `/auth/oidc/callback` | public | Identity-provider callback; redirects to dashboard login completion |
| `POST`   | `/auth/oidc/exchange` | public | body `{code}` → same JWT response as `/auth/login` |
| `GET`    | `/auth/oidc/config` | admin | OIDC provider configuration and callback URL; client secret is omitted |
| `PUT`    | `/auth/oidc/config` | admin | Write OIDC provider configuration; `client_secret` is write-only |
| `POST`   | `/auth/oidc/config/test` | admin | Verify configured discovery metadata and JWKS endpoint |
| `GET`    | `/auth/oauth/status` | public | `{providers:[{kind, display_name, enabled}]}` |
| `POST`   | `/auth/oauth/start` | public | body `{kind, redirect_after?}` → authorization URL |
| `GET`    | `/auth/oauth/callback` | public | App OAuth callback (`code` or DingTalk `authCode`); same completion page as OIDC |
| `POST`   | `/auth/oauth/exchange` | public | Same one-time code exchange as `/auth/oidc/exchange` |
| `POST`   | `/auth/oauth/bind/start` | user | body `{kind, redirect_after?}` → bind the identity to the current user |
| `POST`   | `/auth/oauth/unbind` | user | Unlink one SSO identity by `kind` (requires a local password when it is the last login method) |
| `GET`    | `/auth/oauth/providers/{kind}` | admin | Provider config; `kind` is `oidc`, `feishu`, `dingtalk`, or `wecom` |
| `PUT`    | `/auth/oauth/providers/{kind}` | admin | Upsert provider config; `client_secret` is write-only; WeCom uses `extra.agent_id` |
| `POST`   | `/auth/oauth/providers/{kind}/test` | admin | Test provider credentials |
| `GET`    | `/auth/ldap/status` | public | `{enabled, display_name}` for the login page hint |
| `GET`    | `/auth/passkey/status` | public | `{passwordless_login}` — whether the login page offers “Sign in with a passkey”. Default `false` until an admin enables it |
| `GET`    | `/auth/ldap/config` | admin | Directory settings; `bind_password` is never returned (`has_bind_password` instead) |
| `PUT`    | `/auth/ldap/config` | admin | Upsert directory settings; `bind_password` is write-only (omit to keep the stored one) |
| `POST`   | `/auth/ldap/config/test` | admin | Service bind + probe of `user_base_dn` → `{ok, detail}` |
| `POST`   | `/auth/logout` | user | `204` |
| `GET`    | `/auth/me` | user | `{id, username, role, display_name, locale, ...}` |
| `PATCH`  | `/auth/me` | user | body `{display_name?, locale?, ...}` |
| `POST`   | `/auth/change-password` | user | body `{old_password, new_password}` → `204`, with a replacement access token in `X-Octop-Access-Token`. Outstanding tokens stop working. Accounts without a local password (directory/SSO-provisioned) get `PASSWORD_NOT_SET` |
| `GET`    | `/health` | public | `{status: "ok", version}` |

## Users (admin)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/users` | admin | `[{id, username, role, display_name, email, disabled, ...}]` |
| `POST`   | `/users` | admin | body `{username, password, role, display_name?, email?, permissions?, workspace_root_dir?, token_quota?}` → `201` |
| `GET`    | `/users/{id}` | admin | full user row |
| `PATCH`  | `/users/{id}` | admin | body subset of `{role, display_name, email, disabled, permissions}`; `disabled: true` 停用账号 |
| `POST`   | `/users/{id}/reset-password` | admin | body `{new_password}` → `204` |
| `POST`   | `/users/{id}/unlock-login` | admin | `204` (clears the lockout) |
| `DELETE` | `/users/{id}` | admin | `204` |

## 本人设备接入

管理员继续在“用户管理”创建、停用和重置员工账号；重置口令会使旧口令和旧访问令牌失效，停用后拒绝登录及后续产品请求。

员工在桌面客户端设置中填写企业服务地址，重启后登录，从“本人设备”发起接入。客户端在本机保存 Ed25519 私钥，只发送公钥并签署服务端的一次性挑战。首次申请为 `pending`；管理员在同页的设备管理区域核对员工和设备 ID 后批准为 `approved`，也可撤销为 `revoked`。浏览器页面可以查看状态和管理登记，不能代替桌面客户端证明当前设备。一个设备公钥只绑定一个员工；管理员身份不能接管该员工的设备连接。

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/devices` | 登录用户 | 仅本人设备；管理员调用也仅返回本人设备 |
| `GET` | `/devices/manage` | admin | 全部接入申请及登记状态，不授予电脑操作权 |
| `POST` | `/devices/{device_id}/approve` | admin | 批准本人发起的设备登记；停用账号不能获准 |
| `POST` | `/devices/{device_id}/revoke` | admin | 撤销信任，立即使现有设备会话授权失效 |
| `WS` | `/devices/connect` | 员工访问令牌＋本机私钥证明 | 仅设备所属员工；不接收其他员工身份或执行命令 |

列表字段为 `{device_id, owner_user_id, owner_username, name, platform, status, online, created_at, last_seen}`。时间为 Unix 秒；`online` 仅表示已批准且仍存活的本机连接。信任决定和最近接入时间持久化，在线状态不从历史时间推断，服务重启后须重新连接。

WebSocket 首帧为 `{type:"hello", access_token, public_key, name, platform}`，公钥采用标准 Base64。服务端返回 `{type:"challenge", message}`；客户端对完整 `message` 签名，返回 `{type:"proof", signature}`。挑战仅在该连接使用一次，不接受旧签名重放。成功后服务端返回 `{type:"status", device_id, status, online}`；客户端每 5 秒发送 `{type:"heartbeat", access_token}`，15 秒无心跳即断开。页面登录续期后，桌面客户端立即将新令牌送入原连接，不重新登记；新令牌必须属于原员工，且不能恢复因停用、口令重置或设备撤销而失效的会话。兼容不带令牌的心跳，使用该连接最近一次通过验证的令牌，过期仍拒绝。访问令牌不进入 URL；私钥不进入页面、服务端或审计记录。

子路径部署沿用页面配置的 API 前缀，例如 `/colink/api/devices/connect`；桌面客户端仍只连接设置中的企业服务域名，不接受页面另行指定服务器。

退出登录、切换账号和退出客户端会断开本机连接。停用、重置口令或撤销设备后，状态查询立即显示离线，下一次心跳拒绝继续使用原会话。重新批准已撤销设备后须主动重新接入。新通道不提供任意命令、文件、中央桌面或浏览器操作，也不通过 Bridge 转用他人身份；后续本机任务仍须分别校验设备、目录和动作权限。

本票不新增运行依赖，复用 Go 标准库、已锁定的 WebSocket 库和 `cryptography`，运行不从公网补装。按 2026-11-06 的开发确认，T01 的真实模型任务与麒麟干净环境验收缺口继续保留，分别由 #4/#5 和 #7 补齐；本票的设备接入测试不替代这些验收。

## 本机浏览器只读查询

管理员在用户权限中授予“本机浏览器查询”（`local_browser`）。员工在桌面客户端接入已批准的本人设备，从设备页打开专用浏览器并亲自登录。员工可在浏览器中打开查询结果，或在面板中打开已知的查询地址。例如，打开示例订单 `DEMO-42` 的结果标签，在面板中选择该标签并询问库存数量。答案与来源一同显示，可单独截图核对。

| 方法 | 路径 | 请求与结果 |
| --- | --- | --- |
| `POST` | `/devices/{device_id}/browser/authorize` | 员工令牌及 `X-Colink-Device-Session`。请求 `{action}`，动作可为 `open`、`list`、`new_tab`、`select_tab`、`close_tab`、`query`、`capture` 或 `close`。通过时返回 `{allowed:true}` |
| `POST` | `/devices/{device_id}/browser/query` | 同上。请求 `{instruction,text}`，分别最多 8,000 和 60,000 字符。已授权的企业内网模型返回 `{text}` |

原生客户端在启动浏览器、打开网址及读取页面前分别校验会话与动作权限。查询和截图绑定选中标签的完整地址，地址变化后先刷新标签列表。界面来源省略查询参数和片段。同名同地址的标签需要先在专用浏览器中定位并核对。登录配置与截图留在本机。查询只把提问和提取到的页面可见文字发送给授权的企业模型，禁用模型调用追踪。如果页面内容超出读取上限，或有无法读取的嵌入页面，查询结果会标明内容不完整。业务系统里的填表、提交和附件操作由员工在浏览器窗口内完成。

## 本机 DOCX 任务

管理员须在用户权限中明确授予“本机 DOCX 任务”（`local_docx`）、“读取本机授权文件”（`local_files_read`）和“新建本机文件”（`local_files_create`）。三项默认不授予；管理身份不自动取得本人或其他员工电脑的文件执行权。

员工在桌面客户端的设备页确认当前设备已批准并在线，进入“本机 DOCX 任务”，选择只读或读写目录，填写授权目录直属的 DOCX 文件名与任务描述（不含子目录，文件不超过 20 MB）。读取仅限授权目录；成果必须写入读写目录，成功后显示本机产物路径。新建任务遇到已有文件时拒绝写入。未知命令与任意 shell 执行被拒绝。浏览器页面不能代替本机目录选择及原生设备连接。

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST` | `/devices/{device_id}/docx/authorize` | 员工令牌＋`X-Colink-Device-Session` | `action` 可为 `read`、`create`、`overwrite`、`delete`。校验当前设备归属、在线状态、功能及动作权限，返回 `{allowed:true}` |
| `POST` | `/devices/{device_id}/docx/generate` | 同上，须持有读取及所选写入动作的权限 | `{instruction,text,action?}`，`action` 默认为 `create`，覆盖任务传 `overwrite`。任务描述最多 8,000 字符，必要正文最多 60,000 字符。返回 `{text}`，最多 60,000 字符 |

已批准的 WebSocket `status` 帧增加 `session_token`，仅保留在原生客户端，不透传页面、列表或审计。它与本人访问令牌一起证明当前设备会话；重新连接产生新值，断线、撤销、停用或重置口令后失效。生成前及模型返回后均重新核对权限。客户端在实际读写处另行核对本机目录授权，不能以服务端批准代替目录边界。

原始 DOCX 不上传，客户端仅发送必要正文到企业服务端，再调用已明确配置的活动模型；不向客户端下发统一模型凭据。模型调用无工具，不进入通用 agent 的 shell、MCP 或管理员代理执行链路。活动模型缺失、不可用或生成失败时明确报错，不回落到默认公网服务，也不返回可能包含凭据或原文的异常详情。

本机 DOCX 当前仅支持 OpenAI-compatible 模型协议，须关闭 API fallback。发送正文前，模型地址必须是无用户信息的 HTTP(S) 地址，且全部解析结果属于 RFC1918、环回、链路本地或 IPv6 ULA 地址；公网、混合公网解析、解析失败均拒绝。实际模型调用使用独立 HTTP 客户端，固定已核对的内网 IP，禁止跨源请求、重定向和环境代理，不改变普通聊天客户端。工厂实际地址与当前配置不一致时也拒绝。部署仍须实施断公网网络隔离和企业出站控制；地址检查不能替代防火墙，也不保证内网代理后端不会转发至公网。

本流程核查了锁定的 Harness 1.0.7：`ToolsFilterMiddleware` 仅过滤模型可见工具目录，`FilesystemGuardMiddleware` 不约束 `execute`，本地 Bubblewrap 后端存在回落到主机执行的路径；现有通用 agent 访问检查还允许管理员代理访问。因此本机 DOCX 不将这些默认行为作为授权边界，而由独立本人设备接口与原生客户端的结构化读写操作执行三层校验，不开放 shell 回落。

DOCX 客户端使用 Go 标准库 ZIP/XML，不需运行时下载或额外 Office 依赖；生成基础可编辑正文，不承诺原始复杂版式保真。真实企业模型联测和目标操作系统的断网安装、文件打开编辑验收按开发者确认留到最终验收，受控模型测试不能替代这些结果。

### 本机 XLSX 任务

沿用同一桌面任务面板、目录授权和本机记录，文件类型选择 XLSX。管理员须显式授予 `local_xlsx`、`local_files_read` 和 `local_files_create`；DOCX 权限不隐含 XLSX 权限。

| 方法 | 路径 | 请求与结果 |
| --- | --- | --- |
| `POST` | `/devices/{device_id}/xlsx/authorize` | 原生会话头和员工令牌。`action` 可为 `read`、`create`、`overwrite`、`delete`，返回 `{allowed:true}` |
| `POST` | `/devices/{device_id}/xlsx/generate` | 同上。`{instruction,text,action?}`，`action` 默认为 `create`，覆盖任务传 `overwrite`。`text` 是本机提取的工作表 JSON。返回 `{text}`，内含模型生成的 JSON，实际写入结果由本机任务记录显示 |

数据结构为 `{"sheets":[{"name":"汇总","rows":[["金额",12.5],["总计",{"formula":"SUM(B1:B1)"}]]}]}`。输入另附 `display_rows` 保存日期、百分比和零填充编号的显示含义；原始数值仍在 `rows` 中供计算。日期成果使用可读文本，不保留 Excel 日期类型和原格式。模型仅接收数据，不接收原始 XLSX 或目录凭据；客户端验证结构并计算公式后才写入成果。字符串始终为文本，公式须使用显式对象。失败显示错误，不返回虚假完成状态。

范围：授权目录直属 XLSX，原件与成果不超过 20 MB，解压数据不超过 40 MB；最多 10 张表、合计 10,000 单元格（含表内空白范围），输入与输出 JSON 各不超过 60,000 字符。新建数据表，不保留原样式、图表或宏。公式支持同表有界单元格引用、算术及 SUM/AVERAGE/MIN/MAX/COUNT；拒绝外部链接、跨表引用、循环、错误结果、总引用超过 100,000 或依赖超过 100 层。新建任务遇到已有文件时拒绝写入。

本机任务记录包含文件类型、来源、产物完整路径、状态与失败原因。重启后仍显示记录；运行中断不标为完成。真实模型、目标平台打包运行和 Office 实机编辑须单独验收。

### 本机 PPTX 任务

设备页选择 PPTX。管理员须显式授予 `local_pptx`、`local_files_read` 和 `local_files_create`。以 DOCX 为资料时，还须授予 `local_docx`。客户端打开授权目录内的文件，仅将提取的文字和任务描述经服务端交给内网模型。源文件和生成文件均留在本机。私人专家使用资料库时，任务描述可用于本地检索或发送给内网向量模型。

| 方法 | 路径 | 请求与结果 |
| --- | --- | --- |
| `POST` | `/devices/{device_id}/pptx/authorize` | 员工令牌及 `X-Colink-Device-Session`。`action` 为 `read` 或 `create`，可选 `agent_id` 和 `source_format`。授权成功返回 `{allowed:true}` |
| `POST` | `/devices/{device_id}/pptx/generate` | 同上。接收 `{instruction,text,agent_id?,source_format?}`，`action` 仅接受 `create`。返回 `{text}`，内含模型生成的幻灯片结构。客户端验证结构后写入新文件 |

`source_format` 默认为 `pptx`。例如，员工以 `项目进展.docx` 生成汇报时，客户端传 `source_format: docx`。如果管理员在模型生成期间撤回 DOCX 读取权限，任务失败且成果目录没有新增文件。

例如，员工从只读目录选择 `项目进展.pptx`，填写“按进度和风险整理汇报”，在读写目录生成 `项目汇报.pptx`。任务记录显示完整产物路径。用 PowerPoint 或 LibreOffice 打开文件，确认项目数据正确并可编辑标题和要点。若目标目录是只读目录、已有同名文件或源文件已损坏，任务显示失败原因。

读取支持正文、分组文字和表格文字，源文件上限 20 MB，演示文稿解压数据上限 64 MB，提取文字上限 60,000 字符。若示例文件包含展示季度验收数量的原生图表，任务会提示在演示文稿副本中用表格替换图表后重试。自有汇报技能输出 `{"slides":[{"title":"项目进度","bullets":["已完成 12 项"]}]}`。最多 20 页，每页一条不超过 48 字符的标题及一至五条各不超过 64 字符的要点。成果使用新的版式。源文件中的图像、动画和模板样式不进入成果。

### 本机办公文件的覆盖与删除权限

覆盖和删除 DOCX 或 XLSX 时，管理员须分别显式授予 `local_files_overwrite` 或 `local_files_delete`，同时授予相应格式的任务权限。两项操作权限彼此独立，管理身份和新建权限均不包含它们。服务端授权接口只校验本人设备会话与当前动作权限，目标路径和本次确认留在原生客户端。覆盖生成还需读取权限，生成前后都会核对读取与覆盖权限。删除无需调用模型。

Windows 的读写授权目录支持经确认的覆盖和删除。Linux 使用[受管目录](../desktop/README.md#linux-受管文件目录)执行这两类操作，普通授权目录用于读取和新建。受管服务在提示与执行前，向管理员配置的可信 HTTPS Colink 服务端复核设备和动作权限。授权请求仅包含动作，目标路径和文件留在本机。

例如，员工想把整理后的正文写回授权目录里的 `纪要.docx`。未授予覆盖权限时，`{action:"overwrite"}` 的授权请求返回 `403`。授予后，员工在原生弹窗核对完整路径和覆盖范围。取消会保留原件，确认只替换该文件。

如果执行前原文件被移走或同名文件已被调换，本次任务停止，原文件和同名新文件的内容都保持原样。Windows 在替换中断且保留原件备份时，任务记录会提示核对目标和备份路径。重复提交同一请求编号读取已记录的状态，变更动作或目标须新建任务并确认。

[Harness 1.0.7 的人工确认恢复](https://github.com/TencentCloud/octop-harness/blob/v1.0.7/src/octop_harness/agent.py#L570)保留暂停会话的用户与工具配置。Colink 恢复前核对当前员工、会话和确认编号，并且只接受一次。本机文件操作独立校验本人设备、动作权限和原生确认。

### 本机扫描 PDF 识别

设备页的本机文档任务选择“扫描 PDF（OCR）”，填写授权目录中的 `.pdf` 源文件和新的 `.txt` 文件名，无需任务描述或模型配置。管理员须显式授予 `local_ocr`、`local_files_read` 和 `local_files_create`；其他文档权限与管理员身份不隐含 OCR 执行权。

| 方法 | 路径 | 请求与结果 |
| --- | --- | --- |
| `POST` | `/devices/{device_id}/ocr/authorize` | 原生会话头和员工令牌；`{action:"read"或"create"}`，返回 `{allowed:true}` |

接口只接受动作名称，不接收原始 PDF、识别文字或本机目录。识别由桌面客户端使用随包交付的 pypdfium2、RapidOCR 和本地模型执行，原件和识别文字均留在本机，不调用远端模型。读取和写入分别重新核对本人设备及动作权限；客户端继续校验目录授权、普通文件和新建文件约束，拒绝目录逃逸、只读写入及覆盖已有文件。

支持授权目录直属的扫描 PDF，最多 20 MB、20 页和 60,000 字符。输出为按页标记的 UTF-8 TXT，须对照原件核对，不承诺扫描件版式无损还原。缺少离线识别资源、无效 PDF、识别失败或超限均明确报错。本机任务记录保留来源、成果路径、状态和失败原因，重启后未完成记录标记为中断。Windows x86_64、Linux x86_64 和麒麟 ARM64 的同包离线实机结果须分别记录，不能由本机自动测试替代。

## Agents

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents` | user | `[{id, agent_id, name, persona_mbti, state, unread_count, ...}]` |
| `POST`   | `/agents` | user | body `{name, persona_mbti?, default_model?, system_prompt?, description?, icon?, template_name?, config?}` → `201` |
| `GET`    | `/agents/{id}` | owner | full agent row |
| `PATCH`  | `/agents/{id}` | owner | body subset of create body |
| `DELETE` | `/agents/{id}` | owner | `204` |
| `POST`   | `/agents/{id}/start` | owner | `204` |
| `POST`   | `/agents/{id}/stop` | owner | `204` |
| `POST`   | `/agents/{id}/reload` | owner | `204` (rebuild harness runtime) |
| `POST`   | `/agents/{id}/read` | owner | `204` (mark unread badge cleared) |
| `GET`    | `/agents/{id}/status` | owner | `{state, last_error?, memory_maintenance?, ...}` |
| `POST`   | `/agents/from-expert/{expert_id}` | user | body `{name, ...}` → `201` (creates from bundled expert template) |
| `GET`    | `/agents/{id}/tool-settings` | owner | built-in + installed plugin tools with enable / disableable / available flags |
| `PUT`    | `/agents/{id}/tool-settings` | owner | body `{disabled_builtin: string[], plugins?}` — persists denylist + plugin flags (hot-sync, no reload) |
| `PATCH`  | `/agents/{id}/tool-settings/{tool_name}` | owner | body `{enabled, source, plugin_id?}` — toggle one tool (hot-sync) |

## Skills and skill packages

Skill copy operations create snapshots; they do not keep the source and
destination synchronized after the request completes.

Packages default to private (`shared=false`): only the creator (and admins) can
list or open them. When `shared=true`, other users with the `skill_packages`
permission can view and mount them; only the creator/admin can edit. `copy_policy`
and `allow_download` still apply to users who can already view the package.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/skill-packages` | user (`skill_packages`) | Own packages + shared packages; admins see all |
| `GET` | `/skill-packages?writable_only=true` | user (`skill_packages`) | List only packages the current user may modify; admins may modify all packages |
| `POST` | `/agents/{id}/skill-packages/{package_id}/copy` | owner (`skill_packages`) | body `{skill_slugs: string[], overwrite?: boolean}`; copy selected package skills into the workspace |
| `POST` | `/agents/{id}/skills/{slug}/push-to-package` | owner (`skill_packages`) | body `{package_id, overwrite?: boolean}`; copy a workspace skill into a package created by the user (or any package for admins) |

## Chat (WebSocket)

| Path | Auth | Notes |
|------|------|-------|
| `WS /agents/{id}/chat/ws?token=<jwt>` | owner | Primary dashboard turn endpoint. Send `{"type":"user_turn", ...}` frames (optional `hitl_policy` is a thread-scoped tool-approval bypass); server replies with harness stream chunks ending in `{"type":"done"}` or `{"type":"error","message":"..."}`. `{"type":"ping"}` → `{"type":"pong"}`. `{"type":"subscribe","thread_id"}` → `{"type":"turn_status","thread_id","active"}` plus a `steer.queue_snapshot` baseline (attach to an in-flight turn without cancelling on disconnect). `{"type":"cancel","thread_id"}` stops the active turn and discards its queued steers (explicit stop; disconnect alone does **not** cancel). See [Mid-turn steering](#mid-turn-steering) for steer frames. |
| `GET /agents/{id}/chat/welcome` | agent access | `{welcome_message, quick_prompts, task_examples}`; `task_examples` is `null` when the workspace field is absent |
| `POST /text/assist` | JWT | Unified polish/generate: body `{kind, text?, context?, locale?, default_model?, agent_id?}` → `{text}`. Form kinds require `context.name`; `chat.prompt` requires `text`. Model defaults to automatic routing. |
| `POST /agents/{id}/chat/polish` | owner | **Deprecated** compat alias for `POST /text/assist` with `kind=chat.prompt` |
| `POST /agents/{id}/chat/hitl/resume` | employee who owns the approval | body `{thread_id, pending_id, decisions: [...], hitl_policy?}` → SSE chunk stream. Each pending ID is accepted once for its employee and thread. Missing, expired, or mismatched IDs return an error frame. When the resume stops on another approval, the done frame is `{"type":"done","hitl_paused":true}` and parked steers stay queued. Optional `hitl_policy` (`ask` / `allow_all` / `allow_tools`) applies to this thread. |

Model calls retain LangChain's default retry eligibility and the configured
retry budget/backoff. Exhausted calls raise and remain failed
checkpoint tasks, without a synthetic assistant reply. WebUI, IM and team tasks
display the failure separately. Team rooms publish an `error` frame followed by
`done`; stored error bubbles carry `additional_kwargs.octop_stream_error` for
history display. These diagnostics and automatic team failure notifications do
not become long-term memory.

### Legacy SSE

The previous `POST /agents/{id}/chat/stream` is gone. The dashboard
streams turns over the WebSocket above; HITL resume stays on SSE
because each request is a one-shot continuation.

### Mid-turn steering

While a turn is active, a `user_turn` frame may carry `delivery`
(`auto` \| `steer` \| `follow_up`), `client_pending_id` (client UUID for
correlation) and `expected_turn_id` (the `turn.started` id, for optimistic
concurrency). The server is authoritative: busy `auto`/`steer` steers the
active turn (queued server-side, bounded by depth and byte caps); idle `auto`
starts a normal new turn; `follow_up` while busy is rejected; new steers are
rejected while a HITL card is pending. Replies arrive on the thread:

| Frame | Meaning |
|-------|---------|
| `turn.started` / `turn.resumed` / `turn.ended` | logical turn lifecycle; `turn.ended` carries `hitl_paused: true` when steers stay parked across an approval pause |
| `steer.ack` | direct reply on the sending socket (`queued` / `rejected` / `removed`, with `reason`) |
| `steer.queued` | steer accepted into the active turn queue (`pending_id`, `queue_length`, `preview`) |
| `steer.drained` | queued steers injected as a user message; the same turn continues (`turn.resumed` follows) |
| `steer.discarded` | queue dropped (`turn_cancelled` / `turn_failed` / `continuation_exhausted` / `park_expired`) |
| `steer.removed` / `steer.rejected` | `steer_remove` confirmation / structured rejection |
| `steer.queue_snapshot` | server-authoritative queue baseline (on subscribe) |

Drain happens only at safe boundaries: when tools have run, the first model
output after the completed tool batch preempts the stream (that chunk and
anything after it, except `done` / `error`, are not forwarded) and the queued
steers continue the same logical turn; after a pure-text answer steers drain
at the natural turn end. Image and file blocks on the `user_turn` are queued
with the text and replayed on drain. Steers parked across an HITL pause drain
after the approval resumes, and only when that resume does not stop on a new
approval card. A park older than 30 minutes is discarded (`park_expired`).
`{"type":"steer_remove",...}` withdraws one queued item before it drains. The client puts the draft back only after `steer.ack` with `status: "removed"` and without `already_drained`; an already-drained item stays in the continuation.

## Threads & history

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents/{id}/chat/sessions` | owner | `[{id, thread_id, title, archived, last_active, unread, ...}]` |
| `POST`   | `/agents/{id}/chat/sessions` | owner | body `{session_key?}` → `{thread_id, session_key}` |
| `PATCH`  | `/agents/{id}/chat/sessions/{thread_id}` | owner | body `{title?, pinned?}` → updated row |
| `DELETE` | `/agents/{id}/chat/sessions/{thread_id}` | owner | `204` (archives the active row) |
| `GET`    | `/agents/{id}/chat/sessions/{thread_id}/history` | owner | paginated message history; `turn_active` tells a reconnecting client whether to re-`subscribe` over the chat WebSocket |

**Tool result blocks.** `tool_result` blocks in history (and `tool_result`
frames on the chat WebSocket) carry the tool's return value in `output`. When a
plugin returns a large `octop_ui` payload, the backend offloads the envelope's
`data` field: `output` then contains the slim envelope with
`data_ref: "artifact"`, and the full payload is on the block's `artifact` key
(absent otherwise). An explicit `data` key always takes precedence over
`data_ref` when both appear. Clients that render plugin UIs must resolve
`data_ref` from `artifact`; clients that only read `output` keep working.

### Trajectory ledger

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory` | owner | paginated event summaries; use `before_seq` for older rows |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/events/{event_id}` | owner | full event payload |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/metrics` | owner | aggregated turn, timing, and token metrics |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/stream` | owner | live SSE events; resume with `after_seq` or `Last-Event-ID` |
| `GET` | `/agents/{id}/threads/{thread_id}/trajectory/export` | owner | full ledger download as JSONL (default) or JSON |

## Channels

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents/{aid}/channels` | owner | list of channel rows |
| `POST`   | `/agents/{aid}/channels` | owner | body `{kind, name, config}` → `201` |
| `GET`    | `/agents/{aid}/channels/{cid}` | owner | channel row |
| `PATCH`  | `/agents/{aid}/channels/{cid}` | owner | body subset → updated row |
| `DELETE` | `/agents/{aid}/channels/{cid}` | owner | `204` |
| `POST`   | `/agents/{aid}/channels/{cid}/test` | owner | `{ok, error?}` (instantiate → start → stop) |
| `POST`   | `/agents/{aid}/channels/probe` | owner | `{ok, reason?, detail?}` — preflight a candidate config |
| `POST`   | `/agents/{aid}/channels/{platform}/qrcode/generate` | owner | platform-specific bot creator (wecom, weixin, feishu, yuanbao) |
| `POST`   | `/agents/{aid}/channels/{platform}/qrcode/poll` | owner | poll bot creator state |
| `POST`   | `/agents/{aid}/channels/{platform}/bot-creator/start` | owner | start a bot-creator flow |
| `POST`   | `/agents/{aid}/channels/{platform}/bot-creator/poll` | owner | poll progress |
| `POST`   | `/agents/{aid}/channels/{platform}/bot-creator/stop` | owner | stop an in-flight bot creator |

## Cron

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/settings/timezone` | user | process-level `{timezone}` from `default_timezone` |
| `GET`    | `/settings/upload` | user | `{max_upload_mb, max_upload_bytes}` from `max_upload_mb` |
| `GET`    | `/settings/hitl` | user | `{enabled, tool_guard_require_approval, show_approval_ui}` — `show_approval_ui` is true when tool HITL is on or command guard is `require_approval` |
| `GET`    | `/settings/captcha` | `captcha` | `{active, available, providers, source, v3_min_score}`; secrets omitted |
| `PUT`    | `/settings/captcha` | `captcha` | merge `{active?, providers?}`; empty secret keeps ciphertext; `null` removes a pair |
| `GET`    | `/settings/webauthn` | `captcha` | `{rp_id, origins, passwordless_login}`; `passwordless_login` defaults to `false` |
| `PUT`    | `/settings/webauthn` | `captcha` | merge `{rp_id?, origins?, passwordless_login?}`; omitted fields keep the stored value |
| `GET`    | `/cron/settings` | user | compat alias of `/settings/timezone` |
| `GET`    | `/agents/{aid}/cron/examples` | agent access | `{task_examples}` from workspace `.octop/manifest.json` (`{zh,en}` string arrays, display-normalized to 3 or 6); `null` if the field is absent (dashboard keeps default cards). Prefer `GET /agents/{aid}/chat/welcome` which includes the same field. |
| `GET`    | `/agents/{aid}/cron` | owner only | list cron rows; non-owners (including admin) get `[]` |
| `POST`   | `/agents/{aid}/cron` | owner only | body `{name?, trigger, prompt, session_key?, fresh_thread?, enabled?, model?, task_type?}` → `201` |
| `GET`    | `/agents/{aid}/cron/{cid}` | owner only | cron row |
| `PATCH`  | `/agents/{aid}/cron/{cid}` | owner only | body subset → updated row |
| `DELETE` | `/agents/{aid}/cron/{cid}` | owner only | `204` |
| `POST`   | `/agents/{aid}/cron/{cid}/run-now` | owner only | `204` (fire immediately, off-schedule) |

`task_type` is `"text"` (push prompt directly to the session) or
`"agent"` (run the prompt through the LLM and push the reply).
Default: `"agent"`. `trigger` accepts cron expressions
(`"0 9 * * *"`) plus the `interval:N` / `date:ISO8601` aliases
documented in `infra/cron/trigger.py`. A `date:` job runs once and
then disables itself, whether the run succeeds or fails; the row stays
in the list. If the process is down for more than 24 hours past that
time and the job never ran, `last_status` is `missed` and the job is
disabled. `prompt` must be non-empty and ≤ 2000 characters. `name` is
an optional display label; when omitted, the server derives one from
`prompt`.

## Providers

企业聊天与 embedding 分别在模型设置页配置、启停与测试。统一凭据仅保存在服务端；所有列表及写入响应返回 `api_key_set`，不返回 `api_key`。更新时省略凭据表示保持原值，显式 `null` 表示撤销。`note` 记录企业服务配额、周期和限制，由上游执行；本地分别反馈鉴权、配额/频率、网络、模型和响应格式错误。

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/providers` | user | 服务配置及测试记录，不返回统一凭据 |
| `GET` | `/admin/providers` | providers | 管理配置与模型可见范围 |
| `POST` | `/admin/providers` | providers | `{name, kind, base_url?, api_key?, purpose?, models?, note?}` |
| `PATCH` | `/admin/providers/{id}` | providers | 修改配置或 `enabled`；影响调用的修改清除旧测试记录 |
| `DELETE` | `/admin/providers/{id}` | providers | 被专家引用时拒绝删除 |
| `POST` | `/admin/providers/{id}/test` | providers | `{model_id?, capability?: "chat"\|"stream"\|"tools", embedding?: boolean}` |
| `POST` | `/admin/providers/test-draft` | providers | 测试未保存配置；可用 `provider_id` 引用服务端凭据，但不得更换目标地址或请求头 |
| `POST` | `/admin/providers/fetch-models` | providers | 显式地址的 OpenAI-compatible `/models`；同样支持安全引用服务端凭据 |

本轮依照锁文件安装并检查的组合为 Harness 1.0.7、Gateway 1.0.3、Memory 1.0.3、Browser 1.0.0；未新增依赖，Gateway 继续作为消息通道组件。

测试使用现有 Harness 模型工厂；流式测试要求收到可见内容，工具测试要求返回约定工具及参数（不执行业务工具）。embedding 测试使用与知识库相同的向量校验，返回 `dimension`。保存的 `probe_results` 按模型及能力分别记录结果，单项成功不代表其他能力通过，也不代表持续健康。缺少地址或模型 ID 会明确失败，不回退公网默认服务。

### 知识库请求限制与故障范围

`GET /knowledge-bases/capability` 返回当前配置及 `embedding_batch_size`、`embedding_max_input_chars`。管理员通过 `PUT /knowledge-bases/feature` 随现有启用/模型配置保存这两项：批量为正整数（默认 20）；单条 Unicode 字符上限为非负整数（默认 0，不施加本地上限）。远程调用按批拆分；超长输入明确失败，不截断。上游 token 限制仍由服务端执行，字符上限不能替代 token 配额。

向量结果校验索引、有限数值、非空与维度一致性；库内维度变更不能静默混入旧索引，需走已有重建流程。索引任务失败持久化为失败状态；检索服务/索引异常明确反馈知识库故障，普通聊天继续可用，不转为全文搜索或伪装无匹配资料。

本轮真实内网聊天、流式、工具调用、embedding 维度/配额与 PostgreSQL/pgvector 服务联测留待最终验收。受控测试不代表这些项目已通过；测试配置、启用状态及最近结果均持久化在原有数据库，不新增推理或向量服务。

## Models

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/models/presets` | user | provider templates from `octop-harness` |
| `GET` | `/models` | user | resolved models across enabled providers |
| `GET` | `/models/active` | user | `{provider_name, model}` |
| `PUT` | `/models/active` | admin | body `{provider_name, model}` |

### Media generation models

These instance-wide endpoints require the `providers` permission (administrators bypass
permission checks). The Ark API key is write-only and encrypted at rest. Saving settings
reloads running agents so the image and video tools receive the new configuration.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/admin/media-generation` | providers | Return Volcengine Ark image/video settings; never returns the API key |
| `PUT` | `/admin/media-generation` | providers | Save enabled tools and Seedream/Seedance model IDs; an included API key is verified before saving |
| `POST` | `/admin/media-generation/test` | providers | Test credentials or a selected image/video model; model tests submit real, potentially billable requests |

## Voice

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`  | `/voice/presets` | user | voice provider presets |
| `GET`  | `/voice/providers` | user | user's voice providers |
| `GET`  | `/voice/active` | user | active TTS / STT configuration |
| `PUT`  | `/voice/active` | user | update active voice configuration |
| `POST` | `/voice/stt` | user | body `{audio, format?, language?}` → `{text, segments?}` |
| `POST` | `/voice/tts` | user | body `{text, voice?, format?}` → audio bytes |
| `GET`/`POST`/`PATCH`/`DELETE` | `/admin/voice/providers` | admin | admin voice provider CRUD |

## MBTI & personas

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/mbti/codes` | user | `[{code: "INTJ", ...}, ...]` (16 codes + `_default`) |
| `GET`    | `/mbti/codes/{code}` | user | full profile (dimensions, behaviour, UI metadata) |
| `GET`    | `/mbti/preview/{code}` | user | rendered persona template (legacy `/api/personas/{code}`) |
| `PUT`    | `/agents/{aid}/mbti` | owner | body `{code}` → apply persona and reload |
| `GET`    | `/personas` | user | `[{code}, ...]` (compat shim) |
| `GET`    | `/personas/{code}` | user | rendered template (compat shim) |

Persona content lives in `src/octop/infra/agents/persona/mbti_profiles.py` —
see [Personas](./personas.md).

## Experts

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/experts` | user | bundled expert catalog (includes `task_examples` `{zh,en}` when present) |
| `GET`    | `/experts/{expert_id}` | user | full expert template (SOUL.md, skills, files, `task_examples`) |
| `POST`   | `/agents/from-expert/{expert_id}` | user | body `{name, locale?, quick_prompts?, ...}` → `201`; optional `quick_prompts` overwrites workspace cards after seed |

Bundled experts live in `src/octop/infra/agents/experts/library/`
(en/zh divisions); the catalog is locale-aware via
`Accept-Language` / user preference.

## Workspace, skills, subagents, memory, files

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/agents/{aid}/workspace/tree` | owner | list dir (default `.`) |
| `GET`    | `/agents/{aid}/workspace/file` | owner | read a content file |
| `PUT`    | `/agents/{aid}/workspace/file` | owner | write a content file (via `BackendWorkspace`) |
| `DELETE` | `/agents/{aid}/workspace/file` | owner | delete a content file |
| `POST`   | `/agents/{aid}/workspace/rename` | owner | rename / move |
| `POST`   | `/agents/{aid}/workspace/upload` | owner | multipart upload → backend (not `max_upload_mb`) |
| `GET`    | `/agents/{aid}/workspace/download` | owner | download a file |
| `GET`    | `/agents/{aid}/workspace/mention-index` | owner | bounded `@` file names via the workspace backend (`truncated` when capped; short cache) |
| `GET`    | `/agents/{aid}/workspace/glob` | owner | glob backend paths |
| `GET`    | `/agents/{aid}/workspace/grep` | owner | grep backend files |
| `GET`    | `/agents/{aid}/workspace/...` | owner | see `api/routers/workspace.py` |
| `GET`    | `/agents/{aid}/skills` | owner | list installed skills |
| `GET`    | `/agents/{aid}/skills/{slug}/export` | owner | download one workspace skill as a ZIP (`{slug}/SKILL.md` plus siblings) |
| `POST`   | `/agents/{aid}/skills/export` | owner | body `{slugs: string[]}`; one ZIP, each slug a top-level folder |
| `GET`    | `/skill-packages/{id}/export` | user (`skill_packages`) | download every skill in the package as one ZIP. `allow_download=false` returns 403 for users other than the creator and admins |
| `PUT`    | `/agents/{aid}/skills/{slug}` | owner | enable / disable a skill |
| `GET`    | `/agents/{aid}/skills/hub/search` | user | Skill Hub search |
| `GET`    | `/agents/{aid}/skills/hub/rankings` | user | Skill Hub rankings |
| `POST`   | `/agents/{aid}/skills/hub/install` | owner | body `{slug, version?}` → `201` |
| `GET`    | `/subagent-catalog/divisions` | user | bundled subagent divisions |
| `GET`    | `/subagent-catalog` | user | bundled subagent catalog |
| `GET`    | `/subagent-catalog/{slug}` | user | full subagent definition |
| `GET`    | `/agents/{aid}/subagents` | owner | installed subagents for an agent |
| `POST`   | `/agents/{aid}/subagents` | owner | install a bundled subagent |
| `GET`    | `/agents/{aid}/heartbeat-config` | owner | read heartbeat YAML |
| `PUT`    | `/agents/{aid}/heartbeat-config` | owner | write heartbeat YAML |
| `GET`    | `/agents/{aid}/memory/daily` | owner | list daily memory files |
| `GET`/`PUT` | `/agents/{aid}/memory/strong` | owner | read/edit user-maintained MEMORY.md, including for stopped agents; new sessions snapshot full content once, existing sessions keep their snapshot |
| `GET`/`PUT` | `/agents/{aid}/memory/user` | owner | read/edit user-maintained USER.md with the same session snapshot policy; distinct from the derived profile |
| `GET` | `/agents/{aid}/memory/manual-review` | owner | read persisted reconciliation status against current documents; no model call or memory mutation |
| `POST` | `/agents/{aid}/memory/manual-review` | owner | reconcile saved USER.md + MEMORY.md against up to 100 unchecked atoms; duplicates remain stored, proven conflicts retire the old atom and create a replacement review candidate with evidence |
| `GET`    | `/agents/{aid}/memory/daily/{filename}` | owner | read one daily memory |
| `DELETE` | `/agents/{aid}/memory/daily/{filename}` | owner | delete one daily memory |
| `GET`/`POST` | `/memory/...` | user | memory API (dashboard memory tab) |

### Manual document reconciliation

The manual editors save files first, then request reconciliation. The response reports
`complete`, `partial` (continue checking), `uncertain`, `unavailable`, `failed`, `stale`,
`disabled`, `too_large`, or `not_checked`, plus conflict/duplicate counts and candidate IDs.
Semantic checks use the configured memory auxiliary model and require literal document
evidence; unavailable or uncertain checks never silently retire facts. Combined document
input above 100,000 characters is explicitly refused. Current documents are checked again
when accepting a document-derived candidate; stale candidates require review of the new
version. The file save remains successful if the subsequent semantic check fails.

### Current-expert memory storage maintenance

SQLite restore requires a harness runtime with `MemoryMiddleware.pause_background`
([octop-harness #69](https://github.com/TencentCloud/octop-harness/pull/69)). Older runtimes
reject restore before stopping the expert or changing its database. Independent retry
controls also require this runtime; the extraction config API reports supported options.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET` | `/agents/{aid}/memory/storage/check` | owner/admin | Read-only inspection; backend, scope, per-expert statistics and cleanup preview |
| `GET` | `/agents/{aid}/memory/storage/maintenance` | owner/admin | Current progress and this expert's last result, retained until host restart |
| `POST` | `/agents/{aid}/memory/storage/maintenance` | owner/admin | Body `{confirmed: true}` → `202`; run one host-owned maintenance job |
| `GET` | `/agents/{aid}/memory/storage/backups` | owner/admin | List this expert’s maintenance and pre-restore SQLite backups from disk |
| `DELETE` | `/agents/{aid}/memory/storage/backups` | owner/admin | Body `{confirmed: true, backup_id: "server-generated filename"}` → `204`; permanently delete only the selected backup; rejected while maintenance/restore is active |
| `POST` | `/agents/{aid}/memory/storage/restore` | owner/admin | Body `{confirmed: true, backup_id: "server-generated filename"}` → `202`; back up current state, restore and reload only this expert |

The job waits for the selected expert to become idle and reserves only that
expert's turn admission. Ownership and storage isolation are checked again after
waiting. Other experts' conversations can continue. Shared SQLite files and
colliding PostgreSQL namespaces reject maintenance.

SQLite creates a full file backup before checkpoint deduplication and compaction.
SQLite restore accepts only regular sibling backup files matching the host’s naming scheme; arbitrary paths and symlinks are rejected. It validates SQLite integrity, namespace ownership and checkpoint references, drains this expert’s background workers, closes its runtime, and creates and fsyncs a full current-state backup before overwriting. SQLite’s backup API manages the database and WAL; files are not blindly replaced. A failed post-restore validation rolls back from the safety snapshot. The expert is recreated before waiting conversations are released. Backups remain discoverable after restart. The control-plane conversation lists/message archives, attachments and configuration are not restored.
Its inspection uses a separate read-only connection and a consistent snapshot.
`memory_counts` reports only the selected namespace's logical categories, excluding
full-text index duplicates. `checkpoint_counts` reports checkpoints, writes, shared
content blocks and distinct session IDs in a dedicated expert file. Shared files
omit checkpoint counts because those tables have no expert namespace, and disable
file maintenance, including when residual foreign namespaces are detected.
Each count has `rows`, `status` (`ok`, `missing`, `timed_out`) and `elapsed_ms`;
unavailable counts are null, never zero. Count queries share a five-second budget.
`elapsed_ms` and `file_check_ms` measure server-side inspection only, excluding
network transfer; they are not a performance benchmark. Completed SQLite jobs
include `inspection_before` and `inspection_after` snapshots in `report`, alongside
file/WAL totals, deduplication statistics and backup size. Counts may remain stable
because maintenance preserves history; concurrent background writes may affect them.

PostgreSQL limits every read/delete to the live expert's verified namespace and
backs up exact target rows to a private JSONL file under
`OCTOP_HOME/backups/memory-maintenance/` before committing a single transaction:

- Rejected candidates decided more than 30 days ago, with no atom referencing them.
- Operational journal entries older than 14 days (`extract_run`, `gc_*`,
  `page_regen`, `page_regen_failed`; only the fixed action allowlist is eligible).
- At most 500 rows per category per pass; locked rows are skipped and another pass
  can process remaining eligible records.

Active memories, raw evidence, decision journals (including consolidation), and
all checkpoints remain intact. PostgreSQL results report deleted rows and backup
size, **not physical disk reclamation**. No shared-table `VACUUM` or host-wide
orphan sweep runs from this page. Row-byte statistics are estimates that exclude
indexes, shared overhead and checkpoints. The JSONL header identifies the format,
namespace and row count; subsequent records contain table names and original rows
excluding generated search columns. PostgreSQL JSONL restore is not supported by this endpoint.

To verify the PostgreSQL isolation/backup/rollback gate against a dedicated test
database, set `OCTOP_MEMORY_MAINTENANCE_TEST_DSN` and run
`uv run pytest tests/integration/test_postgres_memory_storage.py -q`. Never point
this gate at production: it creates test namespaces and a temporary failure trigger.

## ACP (Agent Client Protocol)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/acp` | user | current user's global runner list |
| `PUT`    | `/acp` | user | replace global runners |
| `GET`    | `/acp/{runner_name}` | user | one runner |
| `PUT`    | `/acp/{runner_name}` | user | upsert one runner |
| `DELETE` | `/acp/{runner_name}` | user | delete a custom runner (built-ins are protected) |
| `GET`    | `/agents/{aid}/acp` | owner | global runners + this agent's `tool_enabled` flag |
| `PUT`    | `/agents/{aid}/acp` | owner | update `tool_enabled` and optionally the global list |
| `PUT`    | `/agents/{aid}/acp/tool` | owner | toggle the `acp_runner` tool only |

See [ACP integration](./acp.md) for the runner object schema and the
Zed setup example.

## Storage backends

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`POST`/`PATCH`/`DELETE` | `/storage-backends` | user | per-user remote backend connections |
| `GET`/`POST`/`PATCH`/`DELETE` | `/admin/storage-backends` | admin | admin-managed backends |

## Host filesystem (dashboard)

Browse the host OS directory tree when configuring a local backend
`root_dir` (`local_shell` / `filesystem`). Authenticated users only;
sensitive mounts (`/proc`, `/sys`, `/dev`, `/etc`, `/root` on POSIX)
are rejected, except the process home and its subdirectories (so a
server running as root may use `/root` as the default `root_dir`).
Listing is single-level and capped; write probe runs only
for non-`/` paths.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/filesystem/dirs?path=<abs>` | user | `{path, entries: [{path, name}]}` — one directory level |
| `POST`   | `/filesystem/probe` | user | body `{path}` → `{ok, path?}` or `{ok: false, code, detail?}` (`not_directory`, `permission_denied`, `write_failed`, `not_allowed`) |
| `POST`   | `/filesystem/mkdir` | user | body `{path, base_name?}` → `{path, name}` — create child dir (`base_name` defaults to `New Folder`; collisions become `Name (2)`, …) |
| `POST`   | `/filesystem/rename` | user | body `{path, new_name}` → `{path, name}` — rename basename only |

## Connectors & OAuth

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/connectors/catalog` | user | connector catalog (Notion, Figma, …) |
| `GET`    | `/connectors/test-credentials` | user | preflight credentials |
| `GET`    | `/connector-instances` | user | list instances |
| `POST`   | `/connector-instances` | user | create instance |
| `GET`/`PATCH`/`DELETE` | `/connector-instances/{id}` | user | CRUD on an instance |
| `POST`   | `/connector-instances/{id}/test` | user | test a configured instance |
| `POST`   | `/connector-instances/{id}/refresh` | user | refresh OAuth tokens |
| `GET`    | `/connectors/auth/{kind}/info` | user | auth flow info |
| `GET`    | `/connectors/auth/{kind}/authorize-url` | user | build the authorize URL |
| `POST`   | `/connectors/auth/{kind}/exchange-code` | user | exchange auth code |
| `POST`   | `/connectors/oauth/start` | user | start OAuth (catalog or custom MCP via `target`) |
| `POST`   | `/connectors/oauth/{kind}/start` | user | legacy catalog OAuth start |
| `PUT`    | `/connectors/custom-mcp` | user | save custom MCP server map |
| `PATCH`  | `/connectors/custom-mcp/servers/{name}` | user | patch `enabled` / `default_open` on one server |
| `POST`   | `/connectors/custom-mcp/test` | user | probe a custom MCP server (inline spec or saved name) |
| `GET`    | `/connectors/oauth/callback` | public | OAuth redirect target |
| `GET`    | `/connectors/oauth/pending/{state_id}` | user | poll the OAuth result |

Custom MCP OAuth (streamable HTTP, public HTTPS URL only): Octop discovers the authorization
server from the MCP URL (401 / RFC 9728 protected-resource metadata), requires dynamic client
registration (DCR), stores encrypted tokens in the custom MCP spec, and injects `Authorization:
Bearer` when loading tools. Loopback and LAN MCP URLs may use HTTP and do not use remote OAuth
discovery.

## Internal MCP (octop-harness agents)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST`/`GET`/… | `/internal/mcp/*` | public (mTLS / network-isolated) | MCP gateway used by octop-harness agents (not the dashboard) |

## Octop Agent MCP Gateway

External agents call installed Octop experts through the authenticated
Streamable HTTP endpoint:

`POST/GET/DELETE /mcp/agents`

Use the same JWT bearer token as the REST API. Use HTTPS when the endpoint is
available outside the local host. The default token lifetime is 24 hours.

The server exposes three tools:

| Tool | Purpose |
|------|---------|
| `list_octop_experts` | List experts visible to the authenticated user |
| `call_octop_expert` | Start a new isolated expert conversation, or continue one with `thread_id` |
| `resume_octop_expert` | Resume a call that stopped for tool approval or user input |

`call_octop_expert` returns `thread_id`. Store this value in the external
agent's working state. Pass it only when the next task must use the same Octop
conversation. Omit it for a new task. MCP threads cannot attach to dashboard,
CLI, or IM threads.

When a tool needs approval, the call returns `status: "approval_required"` and
an `approval` object. Read `approval.request.action_requests` and
`approval.request.review_configs`. Send one allowed decision for each action to
`resume_octop_expert`. A resumed call can return another approval request.

Example client configuration:

```json
{
  "mcpServers": {
    "octop-agents": {
      "type": "streamable_http",
      "url": "http://127.0.0.1:8090/api/mcp/agents",
      "headers": {
        "Authorization": "Bearer <octop-access-token>"
      }
    }
  }
}
```

The exact transport key differs by MCP client. Some clients use `http`
instead of `streamable_http`.

## Observability & security

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`PUT` | `/admin/observability` | admin | Langfuse configuration (host, project, env) |
| `GET`    | `/admin/security` | admin | global security policy |
| `PUT`    | `/admin/security` | admin | update global policy |
| `GET`    | `/admin/security/tool-guard/rules` | admin | active command guard rules |
| `GET`    | `/admin/security/tool-guard/rules/raw` | admin | editable YAML |
| `PUT`    | `/admin/security/tool-guard/rules/raw` | admin | save YAML |
| `POST`   | `/admin/security/tool-guard/rules/reset` | admin | reset to shipped defaults |
| `GET`    | `/admin/security/defaults` | admin | defaults + rule catalogs |

## TLS (Let's Encrypt)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`  | `/admin/tls/status` | admin | current cert + issuance task status |
| `POST` | `/admin/tls/preflight` | admin | preflight (port 80, DNS) |
| `POST` | `/admin/tls/issue` | admin | start HTTP-01 issuance |

`GET /.well-known/acme-challenge/{token}` is the HTTP-01 challenge
endpoint (public, mounted directly in `api/app.py`).

## Browser, terminal, uploads

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `WS`/`POST`/`GET`/… | `/agents/{aid}/terminal` | owner | AI-assisted remote PTY |
| `GET` | `/agents/{aid}/terminal/context` | owner | recent terminal context for the AI helper |
| `WS`/`POST`/`GET`/… | `/browser/...` | user | octop-browser sessions, live stream, record/replay |
| `POST` | `/browser/shutdown` | user | stop the current user's Octop-managed Chrome |
| `POST` | `/agents/{aid}/upload` | user | multipart upload → `{workspace}/inbound/` |
| `POST` | `/agents/{aid}/files/access-urls` | user | refresh inbound media URLs (signed) |
| `GET`  | `/agents/{aid}/files/{path}` | owner | read an inbound file |
| `POST` | `/downloads/link` | user | multipart `file` → `{url, filename, expires_in}`: short-lived link for in-app browsers (WeChat, …) that cannot save `blob:` downloads; capped at `max_upload_mb` |
| `GET`  | `/downloads/link/{token}` | public (token) | serve that file as an attachment until it expires (10 min); the token is the only credential |

## Updates, ollama, i18n, plugins, slash, preferences

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`POST` | `/update/status`, `/check`, `/upgrade`, `/progress`, `/restart` | admin | in-place server update flow |
| `GET`/`POST`/`DELETE` | `/ollama-models/...` | `ollama_models` | Ollama model discovery + downloads |
| `GET`/`PUT` | `/ollama-models/service` | `ollama_models` | Local daemon toggle; omit `enabled` to set `models_dir` only |
| `GET` | `/i18n/tools` | user | server-owned tool display names (locale-aware) |
| `GET` | `/i18n/locales` | public | available locales + fallback chain |
| `GET` | `/i18n/locales/{locale}/{namespace}` | public | one namespace bundle (errors, tools, channel, slash) |
| `GET`/`POST` | `/preferences` | user | UI preferences (per-user key/value) |
| `GET` | `/slash/commands` | user | slash command catalog for the composer menu |
| `GET`/`POST` | `/plugins` | user | installed plugin list / install flow |
| `POST` | `/plugins/reload` | admin (`plugins`) | reload plugins from disk into process |
| `PATCH` | `/plugins/{id}` | admin (`plugins`) | enable/disable plugin (`{ "enabled": bool }`) |
| `GET` | `/plugins/{id}/ui/{path}` | user | serve prebuilt plugin UI assets (`ui/dist/…`) |
| `DELETE` | `/plugins/{id}` | admin (`plugins`) | uninstall plugin |

## Usage & admin

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`    | `/usage/summary` | user | token usage summary for the current user |
| `GET`    | `/admin/usage` | admin | global token usage summary |
| `GET`    | `/admin/overview` | admin | `{user_count, agent_count, ...}` |
| `GET`    | `/admin/audit-log` | admin | recent audit rows |
| `GET`    | `/admin/metrics` | admin | `{messages_total, stream_errors_total, cron_runs_total, cron_errors_total, agent_active, ...}` |

## Envs

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `GET`/`PUT` | `/agents/{aid}/envs` | owner | env-var preset for an agent's tool calls |
| `GET`/`POST`/`DELETE` | `/envs/presets` | user | reusable presets |

## Error envelope

Every non-2xx JSON response uses:

```json
{ "error": { "code": "AGENT_NOT_FOUND", "message": "...", "details": { } } }
```

`code` matches the `ErrorCode` enum in `octop.infra.errors`; the
server-localized `message` is rendered by `OctopError.to_envelope`
with the locale from `Accept-Language` (falling back to `en`). The
dashboard mirrors every code under `apiErrors.*` in
`dashboard/src/locales/{en,zh}.json`.

| Code | HTTP | Meaning |
|------|------|---------|
| `AUTH_FAILED` | 401 | Bad credentials |
| `TOKEN_EXPIRED` | 401 | JWT past its TTL |
| `LOGIN_LOCKED` | 423 | Too many failed attempts — wait `login_lockout_seconds` or call `/users/{id}/unlock-login` |
| `SETUP_REQUIRED` | 409 | Initial admin not yet created (or wizard not finished) |
| `FORBIDDEN` | 403 | Authenticated but not allowed |
| `NOT_FOUND` | 404 | No such row / route |
| `USER_DISABLED` | 403 | Account flag flipped off |
| `USERNAME_TAKEN` | 409 | Conflict on `users.username` |
| `AGENT_NOT_FOUND` | 404 | Agent row missing or owned by another user |
| `AGENT_FAILED` | 500 | Runtime errored during a call |
| `AGENT_BUSY` | 409 | Operation refused while another is in flight |
| `PROVIDER_NAME_TAKEN` | 409 | Conflict on `providers.name` |
| `PROVIDER_NOT_VISIBLE` | 400 | Agent config references a provider the user can't see |
| `PROVIDER_REFERENCED` | 409 | Delete blocked because agents still reference the row |
| `PROVIDER_TEST_FAILED` | 400 | `/providers/{id}/test` failed |
| `CHANNEL_KIND_UNSUPPORTED` | 400 | `kind` not in registered builders |
| `CHANNEL_INVALID_CREDENTIALS` | 400 | Channel config rejected by the platform |
| `CHANNEL_PROBE_INCOMPLETE` | 400 | Probe couldn't reach the platform |
| `CRON_TRIGGER_INVALID` | 400 | Trigger string did not parse |
| `CRON_PROMPT_INVALID` | 400 | Empty or too-long prompt |
| `SLASH_UNKNOWN` | 400 | `/<cmd>` is not a registered handler |
| `SLASH_BAD_ARGS` | 400 | Slash handler rejected its arguments |
| `ATTACHMENT_UNSUPPORTED_TYPE` | 400 | Chat / inbound attachment rejected (media type not allowed) |
| `ATTACHMENT_TOO_LARGE` | 413 | Chat / inbound attachment exceeds the configured size limit (`max_mb` in details) |
| `WORKSPACE_PATH_INVALID` | 400 | Path outside the agent's workspace |
| `STORAGE_BACKEND_UNREACHABLE` | 502 | Remote backend connect / list failed |
| `CONNECTOR_OAUTH_FAILED` | 400 | OAuth flow could not complete |
| `INTERNAL_ERROR` | 500 | Unhandled exception (logged with traceback) |

### 本机专家与离线审核发布

本机专家复用现有专家记录，但只在本人客户端的 DOCX/XLSX 受控任务中运行，不启动服务端 Harness，不开放服务端工作区或任意脚本。

| 接口 | 用途 |
| --- | --- |
| `GET /api/experts/local/resources` | 本人可用模型、动作、批准技能和知识库 |
| `GET/POST /api/experts/local` | 列出本人专家／创建私人专家 |
| `PUT /api/experts/local/{agent_id}` | 修改本人专家及资源选择 |
| `POST /api/experts/local/{agent_id}/export` | 导出明确配置，拒绝知识库引用、未批准技能和同名技能的多个版本 |
| `POST /api/experts/local/releases/preview` | 管理员预览离线 JSON 包 |
| `POST /api/experts/local/releases` | 管理员确认来源、许可、内容和权限后发布 |
| `GET /api/experts/local/releases` | 查看批准版本；旧版本保留 |
| `POST /api/experts/local/releases/{release_id}/install` | 按安装者自己的模型、动作和资料权限创建私人副本 |

创建／编辑字段为 `name`、`description`、`system_prompt`、`default_model`、`actions`、`skill_package_ids`、`knowledge_base_ids`。动作仅有 `local_docx`、`local_xlsx`，保存和执行均检查当前员工显式授权；管理员身份不代替本机动作权限或私人内容访问权。

DOCX/XLSX 的 `authorize` 与 `generate` 接口均接受可选 `agent_id`。带该字段时，原生客户端在读前、写前传递专家 ID，服务端在模型调用前后复查本人归属、已选资源和模型权限；配置在调用期间改变则拒绝结果。任务历史记录专家 ID。无该字段时沿用活动模型流程。

旧直接发布／刷新／安装专家模板，以及专家和技能公网市场、URL 导入入口拒绝请求。发布不会改变员工、设备和动作授权。实际 API 字段与响应见 `/api/docs`。
