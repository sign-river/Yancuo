# Schema / 数据格式变更说明（阶段 C）

## 变更原因

阶段 C 需要持久化 AI 任务、待审核变更、提示词模板与审计日志。阶段 A/B 的 `schema_version=1` 不含这些表。

## 兼容性影响

| 项 | 说明 |
|----|------|
| `schema_version` | **1 → 2**（加法迁移） |
| 旧库 | 启动时自动迁移；已有题目/资源/版本数据保留 |
| 破坏性 | **无**。不删除、不重命名既有列 |
| 降级 | schema=2 的库不能用仅支持 v1 的旧程序打开（程序会提示升级） |
| `data_format_version` | 仍为 1（跨端题目字段语义未破坏）；AI 审核结构为应用层扩展 |

## 新增表

- `prompts`
- `ai_jobs` / `ai_job_items`
- `review_sessions` / `review_items`
- `audit_logs`
