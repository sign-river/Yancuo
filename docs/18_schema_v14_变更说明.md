# schema v14 变更说明

## 背景

阅读理解、材料题和跨页综合题需要共享同一份材料与原图，但每个小题必须保留独立的答案、解析、复习记录和搜索结果。schema v14 增加 `ProblemSet`，不把共享内容重复写入每个 `Problem`。

## 迁移

| 项目 | 变更 |
| --- | --- |
| `schema_version` | **13 → 14** |
| `problem_sets` | 新增共享材料容器：标题、材料 Markdown、来源和时间戳 |
| `problem_set_assets` | 新增题组共享原图，按顺序保存内容寻址对象引用 |
| `problems.problem_set_id` | 新增可选题组外键 |
| `problems.item_order` | 新增题组内稳定子题顺序 |
| `data_format_version` | 保持 **1** |

迁移完全为加法式。既有普通题仍没有 `problem_set_id`，资产、复习记录、同步和导出语义不变；删除题组不会删除子题，子题会回到独立题状态。

## 边界

本版本只提供正式数据模型与原子入库服务。确认页的独立题/复合题互转和图片顺序编辑属于 `INTAKE-07`；AI 自动复合题建议属于 `INTAKE-06`。
