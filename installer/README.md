# ChipLookup 安装程序构建指南

## 生成安装包

### 1. 安装 Inno Setup 6
下载地址：https://jrsoftware.org/isdl.php
- 推荐安装 **"QuickStart Pack"** (包含编辑器 + 预处理器)

### 2. 编译脚本
**方法 A（图形界面）：**
- 双击 `setup.iss` → Inno Setup 编辑器打开 → 点击工具栏 **"编译" (F9)**

**方法 B（命令行，适合 CI/CD）：**
```bash
# Windows PowerShell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" setup.iss
```

### 3. 产出
生成的安装包位于：`../dist_installer/ChipLookup_Setup_1.0.0.exe`

---

## 安装包特性

| 功能 | 说明 |
|------|------|
| 安装路径 | `%LOCALAPPDATA%\ChipLookup` (无需管理员权限) |
| 开始菜单 | 自动创建 "ChipLookup" 快捷方式 |
| 桌面快捷方式 | 可选（安装向导勾选） |
| 卸载程序 | 控制面板 "程序和功能" 可卸载 |
| 数据目录 | 安装目录下 `data/chip_database.csv` 可读写 |
| 版本升级 | 同 AppId 覆盖安装，保留用户数据 |

---

## 自定义配置

修改 `setup.iss` 顶部 `#define` 即可：
```iss
#define MyAppVersion "1.0.0"      ; 版本号
#define MyAppPublisher "你的名称"  ; 发布者
#define MyAppURL "https://..."     ; 项目地址
```

---

## 分发建议

1. **代码签名**（可选但推荐）：
   ```bash
   signtool sign /f cert.p12 /p 密码 /tr http://timestamp.digicert.com /td sha256 ../dist_installer/ChipLookup_Setup_1.0.0.exe
   ```
   避免 SmartScreen "未识别的应用" 警告。

2. **GitHub Actions 自动构建**：
   - 将 `installer/` 目录提交到仓库
   - 使用 `qrty/setup-inno` action 在 Release 时自动编译并上传安装包

---

## 常见问题

**Q: 安装后数据文件在哪？**
A: `%LOCALAPPDATA%\ChipLookup\data\chip_database.csv`（用户可写，升级不覆盖）

**Q: 怎么修改默认安装路径？**
A: 改 `DefaultDirName={pf}\ChipLookup` (需管理员) 或保持 `{autopf}` (用户目录，免提权)

**Q: 如何静默安装？**
A: `ChipLookup_Setup_1.0.0.exe /VERYSILENT /DIR="C:\MyPath"`