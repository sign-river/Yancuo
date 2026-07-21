# 研错库 Yancuo

考研错题记录与管理系统。本地优先：SQLite 为工作库，Word/PDF 仅为导出格式。

## 文档

| 文档 | 说明 |
|------|------|
| [docs/01_产品功能与本地架构.md](docs/01_产品功能与本地架构.md) | 产品与本地架构草案 |
| [docs/02_云端同步与安卓端设计.md](docs/02_云端同步与安卓端设计.md) | 云端与安卓设计 |
| [docs/03_开发约束与决策记录.md](docs/03_开发约束与决策记录.md) | 范围、选型与强制原则 |
| [docs/04_开发路线图.md](docs/04_开发路线图.md) | 分阶段路线图 |
| [protocol/data-format-v1.md](protocol/data-format-v1.md) | 跨端数据格式 v1 |

## 当前进度

**阶段 D（已完成）**：外部工作区导出/导入、冲突审核、版本记录。  
下一阶段：**E 轻量复习与去重增强**。

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
