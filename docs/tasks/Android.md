# Android 分任务书

> 负责 Android 本地兼容、测试基线和未来云能力。不得直接移植 Windows Python 同步实现。

## 已交付基线

- Android 本地核心数据库与兼容范围内的未加密 `.ebpack` 校验导入。
- 导入过程包含校验和、SQLite 完整性、外键和 staging/previous 恢复保护。
- Android 当前不读写 Windows 专用的笔记录入、AI 审核和部分高版本业务表。

## 当前任务

### AND-001 · 恢复 Android 单元测试基线

- 状态：阻塞
- 阻塞原因：当前环境尚未配置可用的 Android Studio JBR/SDK。
- 实施：配置环境后运行 `:app:testDebugUnitTest`，记录 Gradle、JDK、SDK 和测试结果。
- 验收：测试可重复执行；失败项区分代码问题与依赖解析问题。

### AND-002 · Android 云下载与增量同步立项

- 状态：阻塞
- 依赖：AND-001 完成；同步协议和目标通道稳定。
- 范围：CloudBase 云快照下载以及允许通道的增量同步。
- 边界：共享协议和测试向量可以复用，Windows Python 代码不能直接移植。

## 后置

- 笔记、草稿和完整 Windows UI 的 Android 对等实现尚未承诺。
- Android 云能力必须保持本地资料隔离、显式恢复和冲突确认。
