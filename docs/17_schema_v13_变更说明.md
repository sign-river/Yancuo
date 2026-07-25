# schema v13 变更说明

## 背景

录题不再假设“一个任务项只对应一张图片”。阅读材料、跨页题目和题图组合需要把多张有序原图作为一个识别单元发送给视觉模型，同时仍保留每张图片不可变、可追溯的来源关系。

## 迁移

| 项目 | 变更 |
| --- | --- |
| `schema_version` | **12 → 13** |
| `intake_recognition_units` | 新增录题会话内的有序识别单元及用户选择的识别模式 |
| `intake_recognition_unit_assets` | 新增识别单元与原图的有序多对多成员关系 |
| `intake_candidate_units` | 新增候选题与来源识别单元的关系，保留多图来源 |
| `ai_job_items.recognition_unit_id` | 新增任务项对识别单元的引用；旧单图任务继续使用 `intake_asset_id` |
| `data_format_version` | 保持 **1** |

迁移为加法式：既有 `intake_assets`、候选题与 AI 任务不搬迁、不删除，仍可继续、重试和确认。新建候选题继续写入现有 `intake_asset_id` 作为首张预览图，完整来源由 `intake_candidate_units` 保存。

## 边界

识别单元只描述 AI 输入，不等同于正式题目或 `ProblemSet`。跨页材料共享与可独立复习的子题模型属于后续 `INTAKE-05`；本版本不会自动把多图结果转换为复合题。
