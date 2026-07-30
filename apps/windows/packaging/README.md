# Windows 安装包

安装包采用 PyInstaller `onedir` + Inno Setup 6。固定 `AppId` 支持覆盖升级，
默认按当前用户安装到 `%LOCALAPPDATA%\Programs\Yancuo`；工作数据库和附件默认位于
`%LOCALAPPDATA%\Yancuo`，不属于安装目录，卸载器不会删除它们。

## 构建

```powershell
cd apps/windows
python -m pip install ".[packaging]"
winget install --id JRSoftware.InnoSetup -e
.\packaging\build_installer.ps1
```

产物写入 `packaging/output/`，脚本同时输出 SHA-256。只构建独立可执行目录时使用
`-SkipInstaller`。`build/`、`dist/` 和 `output/` 都是本地临时产物，不进入 Git。

当前安装器使用 Inno Setup 内置英文向导，应用名称和程序界面仍为中文。发布前若有
代码签名证书，应在上传前对安装器签名；仓库和 CI 不保存私钥。

## 安装、升级与卸载验收

```powershell
.\packaging\verify_installer.ps1 `
  -Installer .\packaging\output\Yancuo-0.1.0c1-windows-x64-setup.exe
```

验收脚本只在系统临时目录安装，执行安装态资源/迁移自检，以同一安装包覆盖升级，
再静默卸载。它要求测试哨兵在升级和卸载后仍存在，同时要求程序文件在卸载后消失，
最后清理整份隔离测试资料。

`.github/workflows/windows-installer.yml` 在版本标签或手动触发时使用干净 Windows
runner 重复同一流程，并上传安装包 artifact。
