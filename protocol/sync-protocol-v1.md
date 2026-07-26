# 同步协议 v1（增量 Operation）

> 状态：阶段 J 定稿。变更前须说明原因与兼容性影响。  
> 配套：`protocol/schemas/operation.schema.json`、`protocol/test-vectors/sync-v1/`  
> 实现：Windows `yancuo_win.application.sync_service` + `domain.sync_merge`；安卓增量客户端可后置。

---

## 1. 目标与非目标

**目标**

- 多设备对题目做增量变更交换（Operation 日志）
- 不同字段自动合并；同一正文字段冲突进审核 UI
- **不**依赖「每题一个 Release」

**非目标（v1）**

- 实时推送 / 后台常驻双向同步
- 完整向量时钟 CRDT
- 用 Release 承载高频 op（Release 仍只做完整备份/迁移）

> 补充说明（远端批次通道）：GitLink/GitHub 不提供可由客户端安全追加的
> `changes/*.jsonl` 文件接口。远端增量使用本协议定义的不可变**批次**附件，
> 一批可含多条 Operation；这不等同于“每题一个 Release”，也不改变完整快照
> Release 的恢复职责。

---

## 2. 仓库布局（与备份共存）

在云端仓库或 LocalFolder 镜像中：

```text
.mistakebook/
  repository.json
  latest.json              # 完整备份指针（阶段 G/H）
changes/
  {device_id}/
    ops.jsonl              # 该设备产生的 Operation，一行一条 JSON
devices.json               # 设备登记
locks/
  primary.json             # 推送批次可选锁
tombstones/
  {entity_id}.json         # 可选墓碑副本（删除 op 已足够时可不写）
releases/                  # 完整 .ebpack 快照（非增量通道）
operation-batches/         # 逻辑目录；远端实现对应不可变 Release 附件
```

---

## 3. Operation 记录

见 `schemas/operation.schema.json`。最小示例：

```json
{
  "format": "yancuo-operation",
  "format_version": 1,
  "operation_id": "op_18af…",
  "device_id": "dev_android_02",
  "database_id": "db_…",
  "timestamp": "2026-07-21T15:35:20+00:00",
  "entity_type": "problem",
  "entity_id": "problem_721…",
  "operation": "update",
  "base_revision": 14,
  "new_revision": 15,
  "changed_fields": { "priority": 5 },
  "tombstone": false
}
```

规则：

- `operation_id` 全局唯一；接收方按 id **幂等去重**
- `device_id`、`database_id`、`timestamp`、`entity_id` 必须是非空字符串；revision 必须是非负整数
- `operation` ∈ `create` | `update` | `delete` | `undelete`
- `changed_fields` 仅含实际变更键；`tags` 可为字符串数组（并集合并）
- `delete` 时 `tombstone=true`，并设置 `changed_fields.status="trashed"`（或等价）
- Windows v1 当前只落地 `entity_type=problem`；其他预留实体不会被误套用到题目模型

---

## 4. 字段分类

### 4.1 冲突字段（两端皆改且值不同 → 必须人工）

- `question_markdown`
- `question_latex`
- `correct_answer`
- `solution_markdown`
- `error_analysis`
- `chapter_id`
- `status` / 删除语义（含 `deleted_at`）

### 4.2 可自动合并

- **不同字段**：两端补丁直接并集应用
- **同字段且策略允许**：
  - `tags`：并集
  - `is_favorite`：逻辑或（任一为真则真）
  - `priority` / `mastery` / `notes` / `title` / `user_answer` 等：若两端改成**相同值**可接受；若不同 → **视为冲突**（保守）

---

## 5. 合并算法（字段级）

输入：`base`（共同祖先字段快照）、`local`、`remote`（当前两端快照或补丁还原后的视图）。

对每个字段 `f`：

1. `lc = local[f] != base[f]`，`rc = remote[f] != base[f]`
2. 若仅 `rc` → 取 remote
3. 若仅 `lc` → 取 local
4. 若皆变且值相等 → 取该值
5. 若皆变且值不等：
   - `tags` → 并集
   - `is_favorite` → OR
   - 否则若 `f` ∈ 冲突字段 **或** 任意标量分歧 → **冲突**
6. 冲突项进入 `ReviewSession(source=sync)`，禁止静默覆盖

合并前：若 `sync.create_snapshot_before_merge=true`，必须先做本地 `.ebpack` 或 zip 快照。

---

## 6. 推送 / 拉取

1. 本地写库成功后追加本地 `sync_operations`（未推送）
2. **推送**：`acquire_lock` → 将未推送 op append 到 `changes/{device_id}/ops.jsonl` → 标记已推送 → `release_lock`
3. **拉取**：读取其他设备 `ops.jsonl`，跳过已应用 `operation_id`，按时间排序合并
4. 默认 `conflict_policy=ask`

### 6.1 GitLink/GitHub 远端批次

远端提供商以不可变 Release 附件承载一个推送批次：

```text
tag: yancuo-ops-v1-{profile_id_suffix}-{device_id_suffix}-{batch_id_suffix}
asset: operations.jsonl
```

`operations.jsonl` 是 UTF-8 JSONL，每行仍是本协议第 3 节定义的一条 Operation。
批次 Release body 至少包含：

```json
{
  "format": "yancuo-operation-batch",
  "format_version": 1,
  "batch_id": "batch_...",
  "profile_id": "profile_...",
  "device_id": "dev_...",
  "asset_name": "operations.jsonl",
  "operation_count": 12,
  "sha256": "...",
  "created_at": "2026-07-25T00:00:00+00:00"
}
```

- 先完整上传并校验附件，再创建或发布批次 Release；失败批次不能进入索引。
- `latest.json` 的资料索引可加法增加 `operation_batches`，记录批次 tag、哈希、
  profile、设备和创建时间。未知客户端必须忽略该字段。
- 推送期间沿用仓库写锁；锁只保护“发布批次 + 更新索引”的短事务，不能覆盖或
  修改既有批次 Release。
- 拉取时只读取当前资料（解析别名后的 canonical profile）的其他设备批次，按
  批次哈希和 Operation `operation_id` 去重，并继续使用第 5 节字段级合并。
- 不存在索引或索引损坏时不得按所有 Release 猜测并执行 Operation；客户端只报告
  可恢复错误，用户可使用完整快照恢复。
- 单批大小、Operation 数量、JSON 行大小和下载总量必须设置硬上限；超限批次
  拒绝导入，不能把远端仓库当作无限制消息队列。

当前实现状态：LocalFolder 已接入可变 `changes/` 通道；GitHub 已接入本节定义的
Release 批次通道，并完成私有仓库的锁竞争、附件上传下载、发布中断恢复与两端
字段冲突联调。GitLink 已验证 `version_id` 的旧版本写入可覆盖新版本，不具备
可复现的原子锁语义，仍只允许完整快照。

---

## 7. 与完整备份的关系

| 通道                    | 用途                         |
| ----------------------- | ---------------------------- |
| Release + `latest.json` | 换机、灾难恢复、低频完整快照 |
| `changes/**/*.jsonl`    | 增量字段同步                 |

增量失败时仍可用完整备份恢复；**禁止**用每题 Release 代替 Operation。

---

## 8. 兼容性

- `format_version=1`；未知字段忽略
- 破坏性变更升高 `format_version`
- 本地库 `schema_version>=3` 含 `sync_operations` 表
