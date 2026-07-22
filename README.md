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

**路线图 A–K 主线已完成**（schema v4）。

主线后已落地：

- AI 密钥可在设置写入系统凭据（环境变量优先）
- Windows 主界面现代化（侧栏分页 + 浅色蓝白主题）

**详细条目与强制登记约定见 [`docs/08_完成更新记录.md`](docs/08_完成更新记录.md)。** 每完成一次可交付更新，必须先追加该文档，再刷新本段摘要。

可选后续：安卓增量同步、GitLink/GitHub 增量通道、插件/本地模型等。

协议：`protocol/gmshare-format-v1.md`；schema：`docs/07_schema_v4_变更说明.md`。

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

数据目录默认：`apps/windows/.yancuo_data/`（可用 `YANCUO_DATA_ROOT` 覆盖）。

## 原则摘要

- API 密钥不得明文入仓；配置只用环境变量/凭据引用名
- 第一阶段只做本地稳定功能，不提前做复杂多端同步
- 修改架构或数据格式前先说明兼容性影响
