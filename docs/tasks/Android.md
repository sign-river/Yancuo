# Android 分任务书

> 负责 Android 本地兼容、测试基线和未来云能力。不得直接移植 Windows Python 同步实现。

## 已交付基线

- Android 本地核心数据库与兼容范围内的未加密 `.ebpack` 校验导入。
- 导入过程包含校验和、SQLite 完整性、外键和 staging/previous 恢复保护。
- Android 当前不读写 Windows 专用的笔记录入、AI 审核和部分高版本业务表。
- Android 图片采集在具备“临时原图 → 审核/裁剪 → 仅派生题图入库”的完整链路前保持关闭；不得用直存整张原图的占位实现替代。

## 当前任务

### AND-001 · 恢复 Android 单元测试基线

- 状态：已完成（2026-08-03）
- 实施：使用 Android Studio 自带 JBR 21 与 `%LOCALAPPDATA%\Android\Sdk` 运行 `:app:testDebugUnitTest`；Gradle 8.2、AGP 8.2.2、Kotlin 1.9.22、compile/target SDK 34、min SDK 26。
- 验收：首次依赖解析后 `5 tests`、0 failures、0 errors；第二次无守护进程复验 `BUILD SUCCESSFUL`，22 个任务中 21 个命中缓存，仍为 `5 passed`。SDK XML v4/旧命令行工具警告不影响构建，后续升级工具链时消除。

### AND-002 · Android 云下载与增量同步立项

- 状态：阻塞
- 依赖：AND-001 已完成；等待 SYNC-103 的真实 CloudBase 网关、私有存储和双用户验收完成。
- 范围：CloudBase 云快照下载以及允许通道的增量同步。
- 边界：共享协议和测试向量可以复用，Windows Python 代码不能直接移植。

## 后置

- 笔记、草稿和完整 Windows UI 的 Android 对等实现尚未承诺。
- Android 云能力必须保持本地资料隔离、显式恢复和冲突确认。
