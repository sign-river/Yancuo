# CloudBase 远端网关

本目录是研错库腾讯云 CloudBase 正式远端通道的部署契约。目标环境为上海区 `yancuo-prod`、CloudBase 免费体验版、PostgreSQL。网关承载完整 `.ebpack` 快照、不可变 Operation 批次、资料索引和原子写锁。

客户端不能直连 CloudBase PostgreSQL 或携带管理员密钥。部署名为 `yancuo-cloud-gateway` 的 HTTP 云函数，由它以服务端身份访问 Cloud Storage 和 PostgreSQL；Windows 客户端只保存环境 ID、网关 HTTPS 地址，以及保存在 Windows Credential Manager 中的普通用户登录会话。密码只参与当次登录，不保存；访问令牌到期后使用轮换式 refresh token 自动续期。

## 接入顺序

1. 在 CloudBase 控制台开启用户名密码认证并创建测试用户；邮箱可以作为用户名。正式开放注册前需另行配置邮箱验证码服务和反滥用策略。
2. 确认环境 ID，并将云存储权限设为私有。`yancuo/` 对象前缀由网关首次上传时自动创建。
3. 在 PostgreSQL 的 SQL 编辑器执行 [`postgres/init.sql`](postgres/init.sql)。该脚本不包含账户、密码或环境 ID。
4. 从 [`gateway`](gateway) 部署 Node.js 18+ HTTP 云函数。入口由 `scf_bootstrap` 启动，监听平台提供的 `PORT`。
5. 只在函数服务端配置下列环境变量；数据库连接串和腾讯云密钥不得下发给客户端。
6. Windows 设置中选择“腾讯云 CloudBase”，填写环境 ID、网关 HTTPS 地址和逻辑 repository，再用普通 CloudBase 账户登录。
7. 测试连接，创建逻辑仓库后做一次小型 `.ebpack` 备份和下载恢复验证。

## 函数环境变量

| 名称 | 必需 | 含义 |
| --- | --- | --- |
| `CLOUDBASE_ENV_ID` | 是 | 部署目标环境 ID；部分运行时也可由 `TCB_ENV` 提供。 |
| `DATABASE_URL` | 是 | PostgreSQL 服务端连接串，只保存在函数加密环境变量。 |
| `GATEWAY_PUBLIC_URL` | 是 | 函数最终 HTTPS 地址，用于生成一次性上传 URL。 |
| `PG_SSL` | 否 | 默认为 `verify`，启用 TLS 并校验证书；仅受控调试可显式设为 `no-verify`，仅本地开发可设为 `disable`。 |
| `PG_SSL_CA` | 否 | `PG_SSL=verify` 时可提供 PEM CA 证书；支持用 `\\n` 表示环境变量中的换行，最大 1 MiB。 |
| `PG_CONNECT_TIMEOUT_MS` | 否 | PostgreSQL 新连接与连接池取连接等待上限，默认 10000 ms，可设 1000–60000。 |
| `USER_STORAGE_BYTES` | 否 | 单用户已提交对象与有效上传预留的合计预算，默认 512 MiB。 |
| `USER_REPOSITORIES` | 否 | 单用户逻辑资料库数量，默认 5。 |
| `MAX_RELEASES_PER_REPOSITORY` | 否 | 单资料库 Release 数量，默认 10000，硬上限 100000。 |
| `MAX_ASSETS_PER_RELEASE` | 否 | 单 Release 附件与有效上传会话合计数量，默认 16，硬上限 1000。 |
| `RATE_PER_MINUTE` | 否 | 所有函数实例共享的单用户分钟请求预算，默认 120。 |
| `MAX_ASSET_BYTES` | 否 | 单对象预算，上限固定 512 MiB。 |

所有数值环境变量必须是文档范围内的整数；空值使用默认值，`NaN`、小数、负数和越界值会让函数在启动阶段直接失败，不能静默关闭预算或限流。

`DATABASE_URL` 不得携带 `sslmode`、`sslcert`、`sslkey` 或 `sslrootcert` 查询参数，以免覆盖网关的 TLS 校验对象。正式部署应保留默认 `PG_SSL=verify`；只有在已确认 CloudBase 内网链路和证书条件、且暂时无法取得可信 CA 时，才可把 `no-verify` 作为有记录的临时兼容措施。

环境 ID 只允许 1–64 位字母、数字和连字符，确保普通用户令牌只发送到对应的腾讯 CloudBase 身份域名；网关对身份响应应用 64 KiB 上限。

安装依赖和本地安全测试：

```powershell
cd cloudbase/gateway
pnpm install --frozen-lockfile
pnpm test
```

## HTTP 契约

所有 JSON 操作使用 `POST {gateway_url}/actions/{action}`，请求头包含：

```text
Authorization: Bearer <cloudbase-user-access-token>
X-CloudBase-Environment-ID: <environment-id>
Content-Type: application/json
```

成功响应为 `{"ok": true, "data": {...}}`，失败响应为 `{"ok": false, "error": "可展示的错误"}`。网关调用 CloudBase `/auth/v1/user/me` 验证 Access Token，并以可信 `sub` 作为资料命名空间；客户端传入的 `owner` 仅为旧协议兼容字段，不参与授权。

| 操作 | 请求/响应要点 |
| --- | --- |
| `health` | 验证令牌、环境和数据库连接。 |
| `users/me` | 返回 `login`、`display_name`。 |
| `repositories/list/create/get` | 管理私有逻辑仓库；`create` 强制 `private=true`。 |
| `manifest/read/write` | 读取/写入一个 JSON 索引。`write` 使用 PostgreSQL 单行事务更新。 |
| `releases/list/create/delete` | 管理不可变快照元数据，`body` 为现有客户端写入的 JSON 字符串。 |
| `assets/upload-url` | 返回一次性 `url`、可选 `headers`、`upload_id`；目标为 Cloud Storage 私有对象。 |
| `assets/commit` | 校验对象存在且大小匹配，再把附件记录与快照关联。 |
| `assets/download-url` | 返回一次性私有下载地址。 |
| `locks/acquire/release` | 对同一逻辑仓库执行原子主写入锁；`acquire` 返回 `acquired: true/false`。 |

对象路径必须由函数生成，例如 `yancuo/{sha256(subject)}/{repository}/uploads/{upload_id}/{asset_name}`，不能接受客户端传入的任意路径，也不能暴露原始用户标识。每次上传使用独立对象路径；`assets/commit` 是唯一提交点，同一发布标签下的同名附件一经提交不得覆盖。在它成功前，`manifest/write` 不得指向该快照。

一次性上传地址只允许一个正在执行的 PUT：网关先在 PostgreSQL 中原子认领会话，失败时释放认领以供安全重试。对象已经完整落盘但客户端未收到响应时，同一凭据重试会幂等返回成功，不会覆盖已上传对象；完成状态必须同时具有文件 ID、正确大小和唯一会话更新。过期会话按到期时间每次最多清理 100 条；对象删除失败时保留会话记录，后续清理继续重试，不能先丢失对象索引。

## 安全与边界

- Cloud Storage 设为私有，下载只经函数签发短期 URL。
- PostgreSQL 表启用 RLS；函数使用受控服务端角色，桌面端没有数据库账号。
- 普通用户 Access Token/refresh token 只进系统凭据；CloudBase SecretId/SecretKey、数据库密码均不得写入 TOML、日志或 Git。
- 邮箱注册、找回密码和验证码发送依赖邮件服务配置；没有配置前只允许管理员在控制台创建测试用户，不应开放公开注册入口。
- `locks/acquire` 需采用 [`postgres/init.sql`](postgres/init.sql) 中的 `INSERT ... ON CONFLICT ... WHERE` 事务语义；每次调用由客户端生成随机 `lease_id`，同一设备的不同任务也不能共享租约。
- `manifest/write`、发布增删及附件签发/提交必须携带当前设备 ID 与租约 ID；网关在同一数据库事务内校验并续期有效主写入租约，释放时也必须匹配两者。
- 完整快照与 Operation 批次共享同一原子 manifest 和仓库锁；LocalFolder 仅保留为离线测试通道。
- 资料库数量与存储预算在 PostgreSQL 事务内按用户加 advisory lock；所有网关实例共享同一配额串行化边界，有效上传会话在提交前也占用预算。
- 登录成功后的请求频率由 PostgreSQL 单行 UPSERT 原子计数；函数横向扩容不会把单用户额度按实例倍增，也不在 Node 进程中永久保留用户键。
- Release 删除先在同一事务写入对象删除队列，再移除元数据；Cloud Storage 暂时失败时保留队列并在后续列表/删除操作重试，避免永久孤儿对象。
- Release 说明最多 64 KiB；列表只返回最近 100 个 Release，并只查询这一页对应的附件。数量配额同时计算未过期上传会话，避免用零字节或未提交对象绕过元数据预算。
- 发布说明按 JSON 转义后的实际字节数计费；所有网关 JSON 响应统一限制为 8 MiB，与桌面端接收预算一致，超限时返回固定小型错误包。
