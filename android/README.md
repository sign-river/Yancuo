# 研错库 Android（阶段 I）

Kotlin + Jetpack Compose 客户端，定位：**移动采集与复习**。

## 用 Android Studio 打开

1. 安装 [Android Studio](https://developer.android.com/studio)（建议 Hedgehog / Iguana 或更新，SDK 34）。
2. **File → Open**，选择本目录：`Yancuo/android/`（不要打开仓库根目录）。
3. 复制 `local.properties.example` 为 `local.properties`，填写本机 SDK 路径，例如：
   ```properties
   sdk.dir=C\:\\Users\\你的用户名\\AppData\\Local\\Android\\Sdk
   ```
4. 等待 Gradle Sync 完成。若缺少 Wrapper JAR，可用 Studio 提示的 **Create Gradle Wrapper**，或在本目录执行：
   ```bash
   gradle wrapper --gradle-version 8.2
   ```

## 运行

- 连接真机或启动模拟器（API 26+）。
- 运行配置选择 `:app`，点击 Run。
- 单元测试：`./gradlew :app:testDebugUnitTest`（Windows：`gradlew.bat :app:testDebugUnitTest`）。

## 阶段 I 范围

| 已实现 | 不做（留给 Windows / 后续） |
|--------|-----------------------------|
| 拍照 / 相册导入收件箱 | Word / PDF 导出 |
| 题库浏览、搜索、优先级与状态 | 外部工作区 |
| 今日复习（五档间隔） | AI 识别与审核 |
| 导入 Windows `.ebpack`（未加密） | 云端自动下载 / 增量同步 |
| Token 本地加密存储（占位） | 插件、复杂模板 |

数据根：`filesDir/yancuo_data/`（`error_book.db`、`assets/objects/`、`identity.json`）。

与 Windows 共享：`schema_version=2`、`data_format_version=1`、内容寻址对象库、`.ebpack` v1。

## 云下载 TODO

阶段 I **不**实现从 GitLink/GitHub 下载 `.ebpack`；设置里可保存 Token，供后续阶段使用。
