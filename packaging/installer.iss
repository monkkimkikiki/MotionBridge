; Inno Setup 6 스크립트 — 설치 위치를 고르는 페이지가 포함된 설치 프로그램을 만든다.
; 먼저 PyInstaller 로 dist\MotionBridge 를 만든 뒤:  iscc packaging\installer.iss
#define MyAppName "MotionBridge"
#define MyAppVersion "0.1.0"

[Setup]
AppId={{6E2B6B0A-3C1D-4F57-9C1F-0B7C5A2D9E11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; 설치 위치 선택 페이지를 항상 보여 준다
DisableDirPage=no
DisableProgramGroupPage=yes
OutputDir=..\dist_installer
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 관리자 권한 없이도 (사용자 폴더에) 설치할 수 있고, 설치 시작 때 전체/현재 사용자 중 고를 수 있다
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\MotionBridge\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\MotionBridge.exe"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\MotionBridge.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MotionBridge.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
