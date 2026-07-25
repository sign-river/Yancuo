# Schema v16: 复习学习记录

v16 为完整复习流程增加独立、可追溯的学习数据，不复用 AI/同步变更审核的 `review_sessions`。

- `problems.review_enabled`：控制题目是否进入复习队列；升级前已有题目默认启用，保持原有行为。
- `study_sessions`：保存一次学习的开始、结束、选择条件、题量与完成状态。
- `study_records`：每次评分一条记录，包含查看答案时间、评分、作答/评分时间、间隔与下次复习时间。

数据库时间仍以 UTC 保存，复习日期按 `Asia/Shanghai` 本地日历计算。不会从旧版 Version 记录推断或伪造历史学习数据。
