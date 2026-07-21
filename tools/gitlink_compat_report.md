# GitLink API 兼容性验证报告

- 探测时间：由阶段 G 修订（对照成熟项目 Release 模式）
- 令牌：仅系统凭据 / 环境变量（本报告不含明文）

## 正确用法（成熟模式）

不要把 GitLink 当 GitLab/Gitee 兼容平台。可用路径是 **Release + Attachment**：

| 操作 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 公开读 Release | GET | `/api/{owner}/{repo}/releases` | 公有库可不带令牌 |
| 发布侧列表 | GET | `/api/{owner}/{repo}/releases.json?page=1&limit=100` | `Authorization: Bearer` |
| 上传附件 | POST | `/api/attachments.json`（multipart `file`） | Bearer → 得 `attachment_id` |
| 创建 Release | POST | `/api/{owner}/{repo}/releases.json` | Bearer，body 含 `attachment_ids` |
| 更新 Release | PUT | `/api/{owner}/{repo}/releases/{version_id}.json` | **必须用 version_id** |

发布顺序：先上传附件拿 id → 再 create/update Release → 成功后再更新 `yancuo-latest` 指针。

配置：本地写死 / 设置中填写 `owner/repo`，不要靠全站 `projects.json` 猜测。

## 早期探测中应纠正的说法

| 旧说法 | 纠正 |
|--------|------|
| user / projects 不可用所以 Release 不可靠 | Release API 本身可用；只是不要依赖 user/projects |
| 附件上传「未确认」 | 已按成熟项目确认：`POST /api/attachments.json` |
| 生产只能靠 local_folder | local_folder 仍作离线/开发后端；GitLink 可走同一套备份业务 |
| 应模拟 GitLab user 接口 | 放弃；手动配置 owner/repo |

## 研错库实现

- `GitLinkProvider`：Bearer、附件优先（`assets_first`）、`version_id` 更新
- `CloudBackupService`：按 capabilities 选择「先附件后 Release」或「先目录后拷贝」
- 令牌：设置页 ↔ keyring；不进 TOML / git

## 安全

- 若令牌曾出现在聊天或终端，请在 GitLink 网页轮换。
