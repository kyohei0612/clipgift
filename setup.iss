#define MyAppName "YouTube クリップツール"
#define MyAppVersion "1.0"
#define MyAppPublisher "kyohei"
#define MyAppExeName "app.py"
#define PythonInstaller "python-3.10.0-amd64.exe"
#define SourceDir "C:\Users\kyohei\testsever"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\YouTubeClipTool
DefaultGroupName={#MyAppName}
OutputDir={#SourceDir}\installer_output
OutputBaseFilename=YouTubeClipTool_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
CloseApplications=force
RestartApplications=no

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Files]
; Pythonインストーラー（一時的に使用）
Source: "{#SourceDir}\installer_assets\{#PythonInstaller}"; DestDir: "{tmp}"; Flags: deleteafterinstall

; アプリ本体のPythonファイル
Source: "{#SourceDir}\app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\downloader.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\mp4inchatnagasi.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\auto_update.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\version.json"; DestDir: "{app}"; Flags: ignoreversion

; binフォルダ（ffmpeg等）
Source: "{#SourceDir}\bin\ffmpeg.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "{#SourceDir}\bin\ffprobe.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "{#SourceDir}\bin\audiowaveform.exe"; DestDir: "{app}\bin"; Flags: ignoreversion

; テンプレート・静的ファイル
Source: "{#SourceDir}\templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs
Source: "{#SourceDir}\static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs

; 起動スクリプト
Source: "{#SourceDir}\launcher.vbs"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launcher.vbs"""
Name: "{group}\アンインストール"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launcher.vbs"""

[Run]
; Python 3.10をサイレントインストール（すでに入っていてもOK）
Filename: "{tmp}\{#PythonInstaller}"; Parameters: "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1"; StatusMsg: "Python 3.10 をインストール中..."; Flags: waituntilterminated

; Pythonのパスをbin/python_path.txtに記録（pythonw.exeを優先）
Filename: "cmd.exe"; Parameters: "/c python -c ""import sys,os; p=sys.executable; pw=p.replace('python.exe','pythonw.exe'); exe=pw if os.path.exists(pw) else p; f=open(os.path.join(sys.argv[1],'python_path.txt'),'w'); f.write(exe); f.close()"" ""{app}\bin"""; StatusMsg: "設定を記録中..."; Flags: waituntilterminated runhidden

; 必要なライブラリをpipでインストール
Filename: "cmd.exe"; Parameters: "/c python -m pip install flask werkzeug pillow numpy requests yt-dlp pytubefix fonttools proglog imageio-ffmpeg --quiet"; StatusMsg: "必要なライブラリをインストール中..."; Flags: waituntilterminated runhidden

; インストール完了後に起動するか聞く
Filename: "{sys}\wscript.exe"; Parameters: """{app}\launcher.vbs"""; Description: "今すぐ起動する"; Flags: nowait postinstall skipifsilent
