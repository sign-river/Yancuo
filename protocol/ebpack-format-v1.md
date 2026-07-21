# `.ebpack` 可移植包格式 v1

> 状态：阶段 F 定稿。变更前须说明原因与兼容性影响。  
> 实现：`yancuo_win.import_export.ebpack`  
> 测试向量：`protocol/test-vectors/ebpack-v1/`

---

## 1. 决策（权威恢复路径）

| 选项 | 结论 |
|------|------|
| `snapshot.sqlite` vs `records/*.jsonl` | **采用 `database/snapshot.sqlite` 为唯一权威恢复路径** |
| 原因 | 与本地工作库同构，迁移后直接 `migrate()`；避免双轨漂移 |
| jsonl | **v1 不写入、不读取**；若未来需要增量交换另开 format_version |
| 加密 | v1 **不实现**；`manifest.encrypted=false`，接口预留至阶段 G |

阶段 B 的 `yancuo-local-backup` zip **仍可恢复**，但新备份应优先生成 `.ebpack`。

---

## 2. 文件形态

- 扩展名：`.ebpack`
- 本质：ZIP（`ZIP_DEFLATED`），条目路径使用 `/`
- 建议单包体积控制在 100–500 MB（云分块见文档 02；v1 本地可不分块）

---

## 3. 包内布局

```text
manifest.json
checksums.sha256
database/
  snapshot.sqlite
  migrations.json
assets/
  objects/{sha256[0:2]}/{sha256}{ext}
  index.json
identity.json                 # 可选
settings/
  portable-settings.toml      # 可选；不得含密钥明文
package-signature.json        # 可选占位；v1 可省略
```

---

## 4. manifest.json

```json
{
  "format": "graduate-mistake-book-ebpack",
  "format_version": 1,
  "created_at": "2026-07-21T14:00:00+00:00",
  "application": "Yancuo",
  "app_version": "0.1.0",
  "database_id": "db_…",
  "schema_version": 2,
  "data_format_version": 1,
  "problem_count": 12,
  "asset_count": 20,
  "encrypted": false,
  "encryption": null,
  "authoritative_payload": "database/snapshot.sqlite",
  "chunk": { "index": 1, "total": 1 }
}
```

校验规则：

- `format` 必须等于 `graduate-mistake-book-ebpack`
- `format_version` 必须为 `1`
- `encrypted=true` 在 v1 实现中应 **拒绝**（未实现解密）
- 若 `schema_version` **大于** 当前程序 `SCHEMA_VERSION` → **拒绝恢复**（提示升级软件）
- 若 `schema_version` **小于等于** 程序版本 → 恢复后执行 `migrate()` 升到当前

---

## 5. checksums.sha256

每行：`{sha256_hex}  {relative_path}`（两空格分隔，路径相对包根）。

至少覆盖：

- `manifest.json`（可选：可不含自身，若含则先算内容再写文件）
- `database/snapshot.sqlite`
- `assets/objects/**` 每个对象文件
- `assets/index.json`
- `database/migrations.json`

恢复前：解压到临时目录 → 按表校验 → 失败则删除临时目录并报错（**禁止半导入**）。

---

## 6. database/migrations.json

```json
{
  "schema_version_at_export": 2,
  "data_format_version": 1,
  "note": "Restore uses snapshot.sqlite then app migrate()."
}
```

---

## 7. assets/index.json

```json
{
  "objects": [
    {
      "sha256": "…",
      "relative_path": "objects/ab/ab….jpg",
      "size": 12345
    }
  ]
}
```

对象路径必须与库内 `assets.relative_path` 一致（相对于 `asset_dir`）。

---

## 8. 导出 / 恢复流程

### 导出

1. `engine.dispose()` 释放 SQLite 锁  
2. 复制 `error_book.db` → 包内 `database/snapshot.sqlite`  
3. 复制 `assets/`（含 `objects/`）  
4. 写 `assets/index.json`、`migrations.json`、`manifest.json`  
5. 计算并写入 `checksums.sha256`  
6. 打成 `.ebpack`

### 恢复到目标数据根

1. 打开 zip，读 manifest，做格式/加密/schema 上限检查  
2. 解压到临时目录，校验 checksums  
3. 将 `snapshot.sqlite` 复制为 `{target}/error_book.db`  
4. 用包内 assets 替换 `{target}/assets`  
5. 可选写入 `identity.json`  
6. 删除临时目录  
7. 调用方对目标库执行 `migrate()`（若由应用打开）

**v1 恢复策略**：写入指定空/新数据根；不在同一进程内静默覆盖正在使用的库（UI 提示设置 `YANCUO_DATA_ROOT` 后重启）。

---

## 9. 兼容性

- 加法字段可出现在 manifest；未知字段忽略  
- 破坏性变更：升高 `format_version`，旧读取器拒绝新包  
- 协议变更流程：**先改本文件与 test-vectors，再改代码**
