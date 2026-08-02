# CloudBase 远端网关

本目录是研错库腾讯云 CloudBase 正式远端通道的部署契约。目标环境为上海区 `yancuo-prod`、CloudBase 免费体验版、PostgreSQL。网关承载完整 `.ebpack` 快照、不可变 Operation 批次、资料索引和原子写锁。

客户端不能直连 CloudBase PostgreSQL 或携带管理员密钥。部署名为 `yancuo-cloud-gateway` 的 HTTPS 云函数，由它以服务端身份访问 Cloud Storage 和 PostgreSQL；Windows 客户端只保存环境 ID、网关 HTTPS 地址，以及保存在 Windows Credential Manager 中的网关用户令牌。

## 接入顺序

1. 在 CloudBase 控制台确认环境 ID，并在“云存储”创建私有目录前缀 `yancuo/`。
2. 在 PostgreSQL 的 SQL 编辑器执行 [`postgres/init.sql`](postgres/init.sql)。该脚本不包含账户、密码或环境 ID。
3. 部署 `yancuo-cloud-gateway` 云函数，实现下面的 HTTP 契约。函数服务端配置 CloudBase 环境访问权限和令牌校验密钥；不要下发数据库连接串。
4. 为真实用户签发受限网关令牌，令牌的 `sub` 是资料命名空间。函数须验证它，不能仅相信请求体中的 `owner`。
5. Windows 设置中选择“腾讯云 CloudBase”，填写环境 ID、网关 HTTPS 地址、逻辑 owner/repository，并粘贴网关令牌。令牌会进入系统凭据，配置文件不保存令牌。
6. 测试连接，创建逻辑仓库后做一次小型 `.ebpack` 备份和下载恢复验证。

## HTTP 契约

所有 JSON 操作使用 `POST {gateway_url}/actions/{action}`，请求头包含：

```text
Authorization: Bearer <gateway-token>
X-CloudBase-Environment-ID: <environment-id>
Content-Type: application/json
```

成功响应为 `{"ok": true, "data": {...}}`，失败响应为 `{"ok": false, "error": "可展示的错误"}`。`owner` 与 `repository` 共同组成逻辑仓库标识，函数必须限制为当前令牌有权操作的命名空间。

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

对象路径必须由函数生成，例如 `yancuo/{subject}/{repository}/releases/{tag}/{asset_name}`，不能接受客户端传入的任意路径。`assets/commit` 是上传提交点；在它成功前，`manifest/write` 不得指向该快照。

## 安全与边界

- Cloud Storage 设为私有，下载只经函数签发短期 URL。
- PostgreSQL 表启用 RLS；函数使用受控服务端角色，桌面端没有数据库账号。
- 网关令牌、CloudBase SecretId/SecretKey、数据库密码均不得写入 TOML、日志或 Git。
- `locks/acquire` 需采用 [`postgres/init.sql`](postgres/init.sql) 中的 `INSERT ... ON CONFLICT ... WHERE` 事务语义；不能用“先读再写”。
- 完整快照与 Operation 批次共享同一原子 manifest 和仓库锁；LocalFolder 仅保留为离线测试通道。
