; ChipLookup Inno Setup 安装脚本
; 编译方法：安装 Inno Setup 6 后，双击此文件或右键 "Compile"
; 输出：dist_installer/ChipLookup_Setup_<版本>.exe
;
; 前置：先构建 onedir 产物（dist\ChipLookup\ 整个目录）
;     python tools/fetch_models.py
;     python -m PyInstaller ChipLookup.spec --noconfirm --workpath build_wb
;
; 注意：含白板识别后整包约 2GB（torch + transformers + TrOCR 权重 1.3GB），
; 因此改用 onedir 分发 —— onefile 每次启动都要解压 2GB 到临时目录，
; 冷启动几十秒，不可接受。安装包本体约 1.5GB（lzma 压缩后）。

#define MyAppName "ChipLookup"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "ChipLookup Team"
#define MyAppURL "https://github.com/your-repo/chip-lookup-tool"
#define MyAppExeName "ChipLookup.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist_installer
OutputBaseFilename=ChipLookup_Setup_{#MyAppVersion}
SetupIconFile=chip_lookup.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
; 整包约 2GB，磁盘空间检查阈值同步放大（单位 KB，这里约 4GB）
ExtraDiskSpaceRequired=0
MinVersion=10.0

[Files]
; onedir 产物：exe + _internal（依赖、模型权重）一起铺到安装目录
Source: "..\dist\ChipLookup\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; 首次运行的种子数据库由 src/paths.py: ensure_user_database() 从
; _internal\data\chip_database.csv 拷到 {app}\data\，无需在此单独铺

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Registry]
; 卸载信息（控制面板 "程序和功能" 显示）
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}_is1"; ValueType: string; ValueName: "DisplayName"; ValueData: "{#MyAppName}"
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}_is1"; ValueType: string; ValueName: "DisplayVersion"; ValueData: "{#MyAppVersion}"
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}_is1"; ValueType: string; ValueName: "Publisher"; ValueData: "{#MyAppPublisher}"
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}_is1"; ValueType: string; ValueName: "URLInfoAbout"; ValueData: "{#MyAppURL}"
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}_is1"; ValueType: string; ValueName: "InstallLocation"; ValueData: "{app}"
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}_is1"; ValueType: string; ValueName: "UninstallString"; ValueData: "{uninstallexe}"
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}_is1"; ValueType: dword; ValueName: "NoModify"; ValueData: 1
Root: HKLM; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}_is1"; ValueType: dword; ValueName: "NoRepair"; ValueData: 1
