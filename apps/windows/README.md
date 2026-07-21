# 研错库 Windows 客户端

当前进度：**阶段 F（`.ebpack`）**。

## 运行

```powershell
cd apps/windows
pip install -e ".[dev]"
python -m yancuo_win
```

## ebpack

- **导出 ebpack** / **导入 ebpack**
- 权威数据：`database/snapshot.sqlite` + `assets/objects`
- 恢复前校验 `checksums.sha256`；损坏或 schema 过高会拒绝
- 协议：`protocol/ebpack-format-v1.md`

旧版 zip 备份（`备份(zip)`）仍可用，新迁移请优先 `.ebpack`。
