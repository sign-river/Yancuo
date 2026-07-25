# 本地资料云快照协议 v1

> 状态：Windows `ACCOUNT-04` 的资料发现与快照谱系基础。不引入账号服务器、手机号、密码或应用级登录 token。

## 身份边界

每个本地数据目录的 `identity.json` 包含：

```json
{
  "profile_id": "profile_...",
  "user_id": "usr_...",
  "device_id": "dev_win_...",
  "database_id": "db_...",
  "display_name": "本地用户"
}
```

- `profile_id` 是随机稳定的资料命名空间，用于组织云快照，不是认证凭据。
- `user_id` 记录本地创建者审计来源，资料绑定或合并时不得重写历史值。
- `device_id` 标识写入设备，不能用于推断用户身份。
- GitHub/GitLink 私有仓库权限及其系统凭据 token 是唯一云端读写边界。

旧身份文件缺少 `profile_id` 时，客户端生成随机 ID 并写回；不得从昵称、手机号或设备信息推导。

## 不可变快照

每个完整 `.ebpack` 上传到独立 Release：

```text
data-v1-{profile_id}-{utc_timestamp}-{device_id_suffix}
```

Release body 至少包含：

```json
{
  "format": "yancuo-profile-snapshot",
  "format_version": 1,
  "profile_id": "profile_...",
  "snapshot_id": "snapshot_...",
  "parent_snapshot_id": "snapshot_... 或 null",
  "asset_name": "snapshot.ebpack",
  "sha256": "...",
  "uploaded_at": "2026-07-25T00:00:00+00:00",
  "device_id": "dev_win_...",
  "database_id": "db_...",
  "schema_version": 17
}
```

Release 一旦创建不得覆盖。共享同一父快照、但互不祖先的两个快照表示跨设备分支，不能按时间戳自动覆盖。`.ebpack` manifest 可加法携带同一 `profile_id`，旧读取器忽略未知字段。

## 资料索引与接管

既有 `latest-pointer` / `latest.json` 保存 `yancuo-profile-snapshots` 索引：按 `profile_id` 保存每份资料的最新快照，并可保存 `profile_B -> profile_A` 的资料别名。别名仅在用户确认合并并选择主资料后写入，且不得形成循环。

连接仓库发现本地 `Profile B` 与云端 `Profile A` 时，只能提供恢复 A、保留独立资料、或开始用户确认的合并流程。恢复必须写入用户选择的目标目录，绝不原地覆盖当前资料。合并前必须为双方创建安全快照；资源可按内容哈希去重，题目、笔记和复习字段冲突必须进入确认界面。

当前 v1 实现资料发现、指定资料恢复与资料绑定基础，不承诺自动合并两份非空数据库。
