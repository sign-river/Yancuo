# 研错库 Yancuo

考研错题记录与管理系统。本地优先：SQLite 为工作库，Word/PDF 仅为导出格式。

## 文档

| 文档                                                                | 说明                 |
| ------------------------------------------------------------------- | -------------------- |
| [docs/01\_产品功能与本地架构.md](docs/01_产品功能与本地架构.md)     | 产品与本地架构草案   |
| [docs/02\_云端同步与安卓端设计.md](docs/02_云端同步与安卓端设计.md) | 云端与安卓设计       |
| [docs/03\_开发约束与决策记录.md](docs/03_开发约束与决策记录.md)     | 范围、选型与强制原则 |
| [docs/04\_开发路线图.md](docs/04_开发路线图.md)                     | 分阶段路线图         |
| [docs/08\_完成更新记录.md](docs/08_完成更新记录.md)                 | **每次完成必须登记** |
| [protocol/data-format-v1.md](protocol/data-format-v1.md)            | 跨端数据格式 v1      |

## 当前进度

路线图 A–K 的代码与协议验收范围已落地，当前数据库为 `schema_version=4`，跨端字段语义为 `data_format_version=1`。这里的“完成”指对应阶段的实现已入库，不等于所有后续能力（加密、PDF、远端增量同步）都已提供。

主线后已落地：

- AI 密钥可在设置写入系统凭据（环境变量优先）
- Windows 主界面现代化（侧栏分页 + 浅色蓝白主题）
- 可发布基线第一轮：正式题目写操作补齐 Operation 日志与远端 create 落地；ZIP/`.ebpack`/`.gmshare` 恢复增加路径、体积、校验与回滚保护
- Windows wheel 已内置默认配置与协议 schema，并新增 Python 3.11–3.13 的 Windows CI、静态检查和安装后资源烟测

**详细条目与强制登记约定见 [`docs/08_完成更新记录.md`](docs/08_完成更新记录.md)。** 每完成一次可交付更新，必须先追加该文档，再刷新本段摘要。

当前边界与后续：

- `.ebpack` v1 当前仅支持未加密包；`protocol/encryption-v1.md` 仍是设计占位。
- Word 是当前稳定导出路径，PDF 导出尚未纳入可用能力承诺。
- Android 可导入 Windows `.ebpack`，但尚未实现 GitLink/GitHub 云下载或增量同步。
- GitLink/GitHub 当前以完整 Release 快照为主；LocalFolder 才提供已接入的 `changes/` Operation 通道。
- 插件、本地模型等仍属于可选后续方向。

协议入口：[`protocol/README.md`](protocol/README.md)；分享包规范：`protocol/gmshare-format-v1.md`；schema 变更：`docs/07_schema_v4_变更说明.md`。

## 快速开始（Windows）

```powershell
cd apps/windows
pip install -e ".[dev]"
python -m yancuo_win
```

测试：

```powershell
cd apps/windows
pytest ../../tests -q
```

源码 checkout 运行时数据目录默认：`apps/windows/.yancuo_data/`；wheel 安装后默认使用当前用户数据目录。两者均可用 `YANCUO_DATA_ROOT` 覆盖。

## 原则摘要

- API 密钥不得明文入仓；配置只用环境变量/凭据引用名
- 第一阶段只做本地稳定功能，不提前做复杂多端同步
- 修改架构或数据格式前先说明兼容性影响
