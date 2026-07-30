# 研错库 Yancuo

考研错题、笔记与复习管理系统。项目采用本地优先架构：SQLite 是工作库，Word/PDF 只作为导出格式；AI 和云端能力不能绕过本地数据边界。

## 文档入口

从 [`docs/README.md`](docs/README.md) 开始阅读。

日常协作只需要关注：

- [`docs/问题收集箱.md`](docs/问题收集箱.md)：用户连续提交、尚未排期的问题；
- [`docs/任务书.md`](docs/任务书.md)：各方向阶段、优先级、阻塞和分任务书入口；
- [`docs/完成记录.md`](docs/完成记录.md)：已经实现、验收并提交的结果；
- [`protocol/README.md`](protocol/README.md)：跨端数据、分享、快照和同步协议。

## 当前基线

- Windows 数据库：`schema_version=19`
- 跨端字段语义：`data_format_version=1`
- Windows 最近完整单元测试：`317 passed`
- 路线图 A—K 已完成
- 本地题库、笔记、复习、AI 录题、普通/受限 AI 搜索已经可用
- `.ebpack` 完整快照、LocalFolder 增量同步和 GitHub Operation 批次已经交付
- GitLink 因缺少可靠原子锁，只开放完整快照
- AI 密钥和云端令牌只保存在系统凭据管理器

详细能力与交付历史见 [`docs/完成记录.md`](docs/完成记录.md)，未完成任务见 [`docs/任务书.md`](docs/任务书.md)。

## 快速开始（Windows）

```powershell
cd apps/windows
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m yancuo_win
```

源码 checkout 的默认数据目录为 `apps/windows/.yancuo_data/`；安装后默认使用当前用户数据目录。可用 `YANCUO_DATA_ROOT` 覆盖。

首次使用真实 AI：

1. 打开“设置 → AI 服务”；
2. 保存 Faro Key；
3. 获取并选择支持图片输入的模型，或在接口不可用时手动填写模型 ID；
4. 测试连接并保存设置。

密钥不会写入 TOML；环境变量 `FARO_API_KEY` 优先于系统凭据。

## 验收

```powershell
cd apps/windows
python -m ruff check src ../../tests/unit
python -m compileall -q src/yancuo_win
pytest ../../tests/unit -q
```

Android 测试需要可用的 Android Studio JBR/SDK：

```powershell
cd android
.\gradlew.bat :app:testDebugUnitTest
```

## 核心原则

- API 密钥和访问令牌不得明文入仓；
- AI 搜索只发送本地有限候选，不上传数据库或整库内容；
- 同步、恢复和合并不得静默覆盖资料；
- 修改跨端格式或同步语义前先更新协议；
- 数据迁移必须覆盖备份、重复执行、失败恢复和引用完整性；
- 用户提交的新问题先进入问题收集箱，再进入唯一的方向分任务书。
