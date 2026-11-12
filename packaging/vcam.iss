; vCAM 설치 프로그램 (Inno Setup 6)
; 빌드: .\scripts\build.ps1 이 PyInstaller 결과(<Dist>\vcam)를 만든 뒤 ISCC로 이 파일을 컴파일한다.
;   ISCC /DAppVersion=0.5.1 /DSourceDir=<Dist>\vcam /DOutputDir=<Dist> packaging\vcam.iss
;
; - 관리자 권한 없이 사용자 폴더(%LOCALAPPDATA%\Programs\vCAM)에 설치한다. 앱의 자동 업데이트가
;   설치 폴더 파일을 직접 교체하므로, 사용자 권한으로 쓸 수 있는 위치여야 한다.
; - 설치 위치는 고정한다(폴더 선택 화면 없음). 제거 시 설치 폴더를 통째로 지우므로 다른 폴더를 고르는 사고를 막는다.
; - 설정·녹화 파일(%LOCALAPPDATA%\vcam, 동영상\vcam)은 설치 폴더 밖에 있어 제거해도 남는다.

#ifndef AppVersion
  #error "AppVersion을 지정해야 합니다: ISCC /DAppVersion=x.y.z"
#endif
#ifndef SourceDir
  #error "SourceDir(PyInstaller 결과 폴더)을 지정해야 합니다: ISCC /DSourceDir=...\vcam"
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#define AppRepo "betona1/vcam"

[Setup]
; AppId는 바꾸지 않는다: 업그레이드 설치와 앱의 '설치 버전 동기화'(update_service.UNINSTALL_KEY)가 이 값을 쓴다.
AppId={{79A29413-64D6-45FF-9040-4F24174B655E}
AppName=vCAM
AppVersion={#AppVersion}
AppVerName=vCAM {#AppVersion}
AppPublisher=vaveling
AppPublisherURL=https://github.com/{#AppRepo}
AppSupportURL=https://github.com/{#AppRepo}/issues
AppUpdatesURL=https://github.com/{#AppRepo}/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\vCAM
DisableDirPage=yes
DefaultGroupName=vCAM
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=vcam-setup-{#AppVersion}
SetupIconFile=..\assets\icons\vcam.ico
UninstallDisplayIcon={app}\vcam.exe
UninstallDisplayName=vCAM
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 실행 중인 vCAM은 설치 전에 닫도록 안내한다(Restart Manager).
CloseApplications=yes
RestartApplications=no

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\vCAM"; Filename: "{app}\vcam.exe"
Name: "{autodesktop}\vCAM"; Filename: "{app}\vcam.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\vcam.exe"; Description: "{cm:LaunchProgram,vCAM}"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; 이전 버전의 남은 파일이 새 버전과 섞이지 않게 앱 폴더의 내부 파일을 먼저 비운다.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\.vcam-backup-*"

[UninstallDelete]
; 자동 업데이트가 추가한 파일까지 지운다. 설치 위치가 고정이므로 {app}은 항상 vCAM 전용 폴더다.
Type: filesandordirs; Name: "{app}"
