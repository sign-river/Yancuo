# Schema 变更说明（阶段 K）

## 变更原因

朋友分享包导入需要记录 `origin_package_id` / `origin_problem_id`，以便再次导入时去重。

## 兼容性影响

| 项                    | 说明              |
| --------------------- | ----------------- |
| `schema_version`      | **3 → 4**（加法） |
| 破坏性                | 无                |
| `data_format_version` | 仍为 1            |

## 新增表

- `problem_origins`：本地题 ← 分享包溯源
