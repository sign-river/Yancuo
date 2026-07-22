# 研错库 Windows 客户端

当前进度：路线图 A–K 的代码与协议验收范围已落地，并完成第一轮同步、归档恢复、云锁和 wheel/CI 加固；加密、PDF、远端增量和正式安装包仍未实现。
完成登记见仓库根目录 [`docs/08_完成更新记录.md`](../../docs/08_完成更新记录.md)。

## 运行

```powershell
cd apps/windows
pip install -e ".[dev]"
python -m yancuo_win
```

## 界面结构（2026-07）

- 左侧：题库 / 复习 / AI / 数据 / 设置
- 题库：筛选 · 列表 · 属性；选中后出现上下文操作
- 密钥：设置中保存到系统凭据（不进 TOML）

## ebpack

- **导出 ebpack** / **导入 ebpack**（数据页）
- 权威数据：`database/snapshot.sqlite` + `assets/objects`
- 恢复前校验 `checksums.sha256`；损坏或 schema 过高会拒绝
- 协议：`protocol/ebpack-format-v1.md`

旧版 zip 备份仍可用，新迁移请优先 `.ebpack`。

## 干净安装与构建烟测

项目使用 `src/` 布局，wheel 内置默认 TOML 与工作区 schema。源码 checkout
优先读取仓库根的 `config/` 与 `protocol/`；普通 wheel 安装读取包内资源，
不再依赖固定层级的 `__file__.parents`。

```powershell
cd apps/windows
py -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install ".[dev]"
.\.venv\Scripts\python -m ruff check src ../../tests
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m pip wheel --no-deps . -w dist
```

需要便携数据目录时可设置 `YANCUO_DATA_ROOT`。未设置时，源码 checkout 使用
`apps/windows/.yancuo_data`；安装态使用用户数据目录（Windows 为
`%LOCALAPPDATA%\Yancuo`，Linux/CI 为 `$XDG_DATA_HOME/Yancuo`）。
