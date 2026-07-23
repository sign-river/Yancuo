# schema v6 变更说明

## 背景

schema v5 已能保存 AI 候选区域，但新题录入仍借用 `Problem(status=inbox)` 和 `ReviewItem` 暂存。这会让尚未确认的内容混入题库计数、去重、回收站和通用审核逻辑，也无法为手动录题提供可靠的跨重启草稿。

schema v6 将“录题过程”建模为独立会话。正式 `Problem` 只在用户确认入库时创建。

## 迁移

| 项目 | 变更 |
| --- | --- |
| `schema_version` | **5 → 6** |
| `intake_sessions` | 新增；保存 manual/ai 模式、状态、AI 任务与草稿 JSON |
| `intake_assets` | 新增；保存录题会话拥有的不可变原图引用 |
| `intake_candidates` | 新增；保存 AI 候选字段、区域、不确定项、顺序与处理结果 |
| `ai_job_items.intake_asset_id` | 新增可空外键；新题识别不再必须引用 Problem/Asset |
| 正式题目字段 | 不变 |
| `data_format_version` | 保持 **1** |

## 生命周期

```text
上传图片
  → IntakeSession(ai)
  → IntakeAsset
  → AiJobItem(intake_asset_id)
  → IntakeCandidate
  → 用户确认
  → Problem + Asset + Version + Operation
```

手动录题使用 `IntakeSession(mode=manual, status=draft)` 保存字段和标签，选中的图片会复制到对象库并登记为 `IntakeAsset`。清空表单或成功入库后删除草稿记录。

## 兼容性

- 迁移仅新增表和可空列，不改写已有 Problem、ReviewItem 或 AI 任务；
- 新录题使用专用 intake 路径；
- schema v5 及更早版本创建的未完成 AI 任务仍按旧路径恢复和处理；
- `.ebpack` 的数据库快照会包含 intake 表，但跨端正式题目字段语义不变；
- Android 将 schema 兼容上限提升为 6，但不在 UI 中展示 Windows intake 过程数据。

## 删除与资源

- intake 原图与正式题目原图可共享同一内容寻址文件，但数据库记录分别归属各自生命周期；
- 删除错误候选只改变 intake 候选状态，不创建回收站题目；
- 已提交候选保存 `problem_id` 作为追踪信息；正式题目永久删除时该引用会被置空；
- 物理对象文件只有在没有任何正式或 intake 记录引用时才可作为孤儿清理。
