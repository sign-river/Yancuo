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
