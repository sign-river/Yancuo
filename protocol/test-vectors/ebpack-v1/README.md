# ebpack v1 测试向量说明

本目录用于固定校验算法与最小样例约定。

## 算法

- 文件哈希：SHA-256，小写 hex  
- `checksums.sha256` 行格式：`{hex}  {path}`（两个空格）  
- 路径使用 POSIX `/`，相对于包根

## 最小合法包必备条目

1. `manifest.json`（`format=graduate-mistake-book-ebpack`, `format_version=1`, `encrypted=false`）  
2. `database/snapshot.sqlite`  
3. `database/migrations.json`  
4. `assets/index.json`  
5. `checksums.sha256`（至少覆盖 snapshot 与 index）

损坏向量：任意改动 `snapshot.sqlite` 字节而不更新 checksums → 恢复必须失败。

具体二进制样例由单元测试在临时目录生成（不强制提交大文件）。
