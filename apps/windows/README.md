# 研错库 Windows 客户端

当前进度：**路线图 A–K 已完成**；近期含 AI 密钥凭据化、主界面现代化。  
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
