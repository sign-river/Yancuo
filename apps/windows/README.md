# 研错库 Windows 客户端

阶段 A：工程骨架、配置、本地库表与主窗口壳。

## 环境

- Python 3.11+
- 依赖见 `pyproject.toml`

```powershell
cd apps/windows
pip install -e ".[dev]"
```

## 启动

```powershell
# 在仓库根目录或 apps/windows 下均可
python -m yancuo_win
```

或安装后：

```powershell
yancuo
```

默认数据目录：`apps/windows/.yancuo_data/`（可用环境变量 `YANCUO_DATA_ROOT` 覆盖）。

## 迁移

启动时自动执行迁移。也可手动：

```powershell
yancuo-migrate
# 或
python -m yancuo_win.data.migrate_cli
```
