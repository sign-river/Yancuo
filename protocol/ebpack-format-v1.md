# `.ebpack` 可移植包格式 v1

> 状态：阶段 F 定稿；Windows 支持导出/导入，Android 支持导入 v1 包。变更前须说明原因与兼容性影响。
> 当前包版本：Windows `schema_version=22`、`data_format_version=1`。
> 实现：Windows `yancuo_win.import_export.ebpack`；Android `EbpackImporter`。
> 测试向量：`protocol/test-vectors/ebpack-v1/`

---

## 1. 决策（权威恢复路径）

| 选项 | 结论 |
|------|------|
| `snapshot.sqlite` vs `records/*.jsonl` | **采用 `database/snapshot.sqlite` 为唯一权威恢复路径** |
| 原因 | 与本地工作库同构，迁移后直接 `migrate()`；避免双轨漂移 |
| jsonl | **v1 不写入、不读取**；若未来需要增量交换另开 format_version |
| 加密 | v1 **不实现**；`manifest.encrypted` 必须为 `false`，加密规范仍待定 |
| 派生搜索索引 | `search_documents_fts` 不进入快照；Windows 恢复后按投影重建，避免依赖 Android 系统 SQLite 的 trigram tokenizer |

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
package-signature.json        # 可选；v1 实现不要求
```

---

## 4. manifest.json

```json
{
  "format": "graduate-mistake-book-ebpack",
  "format_version": 1,
  "created_at": "2026-07-21T14:00:00+00:00",
  "application": "Yancuo",
  "app_version": "0.1.0c1",
  "database_id": "db_…",
  "schema_version": 9,
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
- `encrypted=true` 在 v1 实现中应 **拒绝**（未实现解密；见 `encryption-v1.md`）
- 若 `schema_version` 不在当前读取器声明的范围内 → **拒绝恢复**（提示升级软件）；Windows 上限使用 `SCHEMA_VERSION=9`，Android 导入上限使用独立的 `MAX_EBPACK_SCHEMA_VERSION=9`
- Windows 对低版本快照恢复后执行 `migrate()` 升到当前；Android 保留受支持的加法式快照，不执行 Windows 迁移，也不宣称支持新增表的 UI
- 不可信归档必须流式限量解压：最多 10000 个条目、单条目最多 256 MiB、全部展开内容最多 512 MiB；拒绝绝对/父级/驱动器路径、规范化后重复路径和异常压缩比。Android 在解压前还将外部 `.ebpack` 缓存副本限制为 512 MiB

---

## 5. checksums.sha256

每行：`{sha256_hex}  {relative_path}`（两空格分隔，路径相对包根）。

至少覆盖：

- `manifest.json`
- `database/snapshot.sqlite`
- `assets/objects/**` 每个对象文件
- `assets/index.json`
- `database/migrations.json`

校验表不得为空、不得重复路径，并必须完整覆盖上述固定载荷以及 `assets/index.json` 声明的每一个对象。恢复前：安全解压到临时目录 → 校验完整覆盖、对象索引和实际文件 → 失败则删除临时目录并报错（**禁止半导入**）。

---

## 6. database/migrations.json

```json
{
  "schema_version_at_export": 9,
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

对象路径必须与数据库资产引用表中的相对路径一致，包括 `assets`、`intake_assets`、`note_assets` 和 `note_intake_assets`（均相对于 `asset_dir`）。

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

1. 打开 zip，读 manifest，做格式、加密和读取器 schema 上限检查
2. 安全解压到临时目录，校验 checksums 完整覆盖、对象索引、对象哈希与 `asset_count`
3. 核对 manifest 与快照 schema，执行 SQLite 完整性、外键、版本对应核心表/列及题目计数检查
4. 将数据库、assets 和可选 identity 复制到 staging
5. 原位置恢复时先把现有数据移动到 previous，再从 staging 安装新数据
6. 复核安装后的数据库；失败则删除半成品并从 previous 回滚
7. Windows 对低版本目标库执行 `migrate()`；Android 重新打开已验证的受支持快照
8. 成功后清理 previous 与临时目录

**v1 恢复策略**：Windows UI 写入指定目标数据根并提示切换 `YANCUO_DATA_ROOT` 后重启；Android 的用户触发导入可替换当前本地库，但必须使用 staging + previous + 失败回滚，不能先删除旧库再复制。当前实现只承诺未加密包；加密包会在校验阶段拒绝。

---

## 9. 兼容性

- 加法字段可出现在 manifest；未知字段忽略  
- 破坏性变更：升高 `format_version`，旧读取器拒绝新包  
- 当前 Windows 读取器支持 `format_version=1` 且 `schema_version<=9` 的未加密包；Android 导入器可校验并保留 `schema_version<=9` 的加法式 Windows 快照，但其 UI 只使用题目核心字段。`data_format_version` 当前固定为 `1`。
- manifest 与 `snapshot.sqlite` 内的 `meta_kv.schema_version` 必须一致；读取器还应执行 SQLite 完整性检查，发现损坏或外键错误时拒绝替换本地数据。
- `asset_count` 表示 `assets/objects/` 中按内容哈希去重后的实际对象文件数，覆盖题目、正式笔记和暂存会话引用的对象。
- 协议变更流程：**先改本文件与 test-vectors，再改代码**
