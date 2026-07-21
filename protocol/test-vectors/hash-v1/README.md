# 跨端哈希测试向量 hash-v1

Windows 与 Android 内容寻址对象库必须对同一字节流得到相同 SHA-256。

## 输入

十六进制前缀 + ASCII 后缀（无中间分隔）：

| 部分          | 内容                                       |
| ------------- | ------------------------------------------ |
| JPEG SOI 前缀 | `FF D8 FF`（3 字节）                       |
| ASCII         | `yancuo-hash-vector`（UTF-8 / ASCII 相同） |

完整字节序列（hex）：

```text
ffd8ff79616e63756f2d686173682d766563746f72
```

## 期望输出

小写 hex SHA-256：

```text
bb35a354143fe5e6514b4c23ec0ac62f1f6c82d515c5d3989aa5b33eb3ea2bc6
```

## 对象路径布局

```text
objects/{sha256[0:2]}/{sha256}{ext}
```

对本向量（文件名 `vector.bin`）：

```text
objects/bb/bb35a354143fe5e6514b4c23ec0ac62f1f6c82d515c5d3989aa5b33eb3ea2bc6.bin
```

无扩展名时两端默认后缀为 `.bin`。

## 实现对照

| 端      | 位置                                                                              |
| ------- | --------------------------------------------------------------------------------- |
| Windows | `yancuo_win.assets.object_store.ObjectStore.hash_file`                            |
| Android | `cn.yancuo.android.data.assets.ObjectStore.hashFile` / 单元测试 `ObjectStoreTest` |

相同内容不得覆盖已有哈希文件（内容寻址、只增不改）。
