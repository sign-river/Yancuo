# 本地资料云快照协议 v1

> 状态：Windows `ACCOUNT-04` 的资料发现与快照谱系基础。不引入账号服务器、手机号、密码或应用级登录 token。

## 身份边界

每个本地数据目录的 `identity.json` 包含：

```json
{
  "profile_id": "profile_...",
  "last_snapshot_id": "snapshot_... 或空",
  "user_id": "usr_...",
  "device_id": "dev_win_...",
  "database_id": "db_...",
  "display_name": "本地用户"
}
```

- `profile_id` 是随机稳定的资料命名空间，用于组织云快照，不是认证凭据。
- `last_snapshot_id` 是本机最后确认的云端共同祖先；若当前云端资料头不同，客户端必须暂停上传并要求恢复或合并确认。
- `user_id` 记录本地创建者审计来源，资料绑定或合并时不得重写历史值。
- `device_id` 标识写入设备，不能用于推断用户身份。
- CloudBase 网关按令牌主体执行命名空间授权；客户端只保存系统凭据中的受限网关 token，不持有云存储或 PostgreSQL 管理凭据。

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

上传前，客户端比较本机 `last_snapshot_id` 与云端该资料的最新 `snapshot_id`。云端存在资料头且两者不相等时，上传必须失败；用户只能先恢复最新快照，或进入显式合并流程。成功上传后，本机才将 `last_snapshot_id` 更新为新快照 ID。

## 资料索引与接管

既有 `latest-pointer` / `latest.json` 保存 `yancuo-profile-snapshots` 索引：按 `profile_id` 保存每份资料的最新快照，并可保存 `profile_B -> profile_A` 的资料别名。别名仅在用户确认合并并选择主资料后写入，且不得形成循环。

连接仓库发现本地 `Profile B` 与云端 `Profile A` 时，只能提供恢复 A、保留独立资料、或开始用户确认的合并流程。恢复必须写入用户选择的目标目录，绝不原地覆盖当前资料。合并前必须为双方创建安全快照；资源可按内容哈希去重，题目、笔记和复习字段冲突必须进入确认界面。

## 显式资料合并

两份非空资料只能由用户从账户页发起合并。执行前，程序必须为当前本地资料
创建一个新的不可变快照；被选远端资料必须已有可校验的不可变快照。合并不会
覆盖该两份快照，也不会改写其中的历史 `user_id`、`device_id` 或审计记录。

程序先生成只读预检：按业务表主键比较本地与远端记录，并列出远端新增记录、
完全相同记录和同一记录中实际不同的字段。远端新增记录可纳入合并；同一主键
且字段不同的记录必须逐字段由用户选择保留本地值或远端值。没有明确选择的
冲突字段一律保留本地值，不能静默采用远端值。

合并在一个 SQLite 事务中执行。派生的全文搜索索引不参与合并，完成后由本地
程序重建；内容寻址资源只在目标缺失时按哈希复制。若任一数据库写入、资源复制
或完整性检查失败，事务回滚并保留合并前本地资料。

用户必须选择合并后的主资料 ID。成功后把另一资料 ID 写为主资料的别名，并将
当前设备绑定到主资料；别名不得形成循环。合并后的本地资料在用户再次明确执行
云备份前不会自动上传。

当前 v1 实现资料发现、指定资料恢复、资料绑定和显式字段级合并；不提供后台
自动合并或自动上传。
