#define MyAppName "ClipGift"
#define MyAppVersion "1.0.110"
#define MyAppPublisher "kyohei"
#define MyAppExeName "app.py"
#define PythonInstaller "python-3.10.0-amd64.exe"
; SourcePath は Inno Setup の組込みマクロ（.iss ファイルのあるディレクトリ、末尾 \ 付き）
; Copy() で末尾 \ を除去 → 既存の {#SourceDir}\path 形式と互換
; これでプロジェクトフォルダ名 / 配置場所が変わっても自動追従する
#define SourceDir Copy(SourcePath, 1, Len(SourcePath) - 1)

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\ClipGift
DefaultGroupName={#MyAppName}
OutputDir={#SourceDir}\installer_output
OutputBaseFilename=ClipGift_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#SourceDir}\installer_assets\ClipGiftLog.ico
PrivilegesRequired=lowest
CloseApplications=force
RestartApplications=no

; ライセンス credential の保管場所について（仕様 Q1=B）
;   credential は %APPDATA%\Roaming\ClipGift\license.dat に保存される。
;   インストール先は {localappdata}\ClipGift で別ディレクトリのため、
;   アンインストール時に Inno Setup が削除する範囲には含まれない。
;   → 再インストール時に同一マシンであれば自動再認証される（Q1=B 仕様）。
;   credential を本当に消したい場合はユーザーが手動で APPDATA\Roaming\ClipGift を削除する。

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Files]
; Pythonインストーラー（一時的に使用）
Source: "{#SourceDir}\installer_assets\{#PythonInstaller}"; DestDir: "{tmp}"; Flags: deleteafterinstall

; アプリ本体のPythonファイル
Source: "{#SourceDir}\app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\downloader.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\downloader_twitch.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\twitch_chat.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\twitch_video.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\chat_filter.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\mp4inchatnagasi.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\auto_update.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\version.json"; DestDir: "{app}"; Flags: ignoreversion

; 分離済みPythonモジュール (P1 リファクタで新設)
Source: "{#SourceDir}\chat_analyzer.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\font_manager.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\system_utils.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\paths.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\config.py"; DestDir: "{app}"; Flags: ignoreversion

; ライセンス認証モジュール (2026-05 追加、1 プラン制 / 後方互換 LITE/STD/EXT)
Source: "{#SourceDir}\licensing\*"; DestDir: "{app}\licensing"; Flags: ignoreversion recursesubdirs; Excludes: "__pycache__\*,*.pyc"

; サポートセンター（アプリ側のみ配布。kyohei ローカル運用ファイルは含めない）
Source: "{#SourceDir}\support_center\__init__.py"; DestDir: "{app}\support_center"; Flags: ignoreversion
Source: "{#SourceDir}\support_center\config.py"; DestDir: "{app}\support_center"; Flags: ignoreversion
Source: "{#SourceDir}\support_center\error_reporter.py"; DestDir: "{app}\support_center"; Flags: ignoreversion
Source: "{#SourceDir}\support_center\pii_masker.py"; DestDir: "{app}\support_center"; Flags: ignoreversion

; ドキュメント
Source: "{#SourceDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\CLAUDE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\ISSUES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion

; binフォルダ（ffmpeg等）
Source: "{#SourceDir}\bin\ffmpeg.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "{#SourceDir}\bin\ffprobe.exe"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "{#SourceDir}\bin\audiowaveform.exe"; DestDir: "{app}\bin"; Flags: ignoreversion

; テンプレート・静的ファイル
Source: "{#SourceDir}\templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs
Source: "{#SourceDir}\static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs

; アイコン
Source: "{#SourceDir}\installer_assets\ClipGiftLog.ico"; DestDir: "{app}"; Flags: ignoreversion

; 起動スクリプト
Source: "{#SourceDir}\launcher.vbs"; DestDir: "{app}"; Flags: ignoreversion

; エンドユーザー用リカバリスクリプト（auto_update.py のコメントで「除外しない」と明記されている配布対象）
Source: "{#SourceDir}\scripts\refresh_icon_cache.bat"; DestDir: "{app}\scripts"; Flags: ignoreversion

[Icons]
; PrivilegesRequired=lowest と整合させるため、ユーザー側のデスクトップ /
; スタートメニューに作成する（{commondesktop} だと全ユーザー共通 = 管理者権限必須で
; IPersistFile::Save 0x80070005 を起こす）
Name: "{group}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launcher.vbs"""; IconFilename: "{app}\ClipGiftLog.ico"
Name: "{group}\アンインストール"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\launcher.vbs"""; IconFilename: "{app}\ClipGiftLog.ico"

[Run]
; Python 3.10をサイレントインストール（すでに入っていてもOK）
Filename: "{tmp}\{#PythonInstaller}"; Parameters: "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1"; StatusMsg: "Python 3.10 をインストール中..."; Flags: waituntilterminated

; Pythonのパスをbin/python_path.txtに記録（pythonw.exeを優先）
Filename: "cmd.exe"; Parameters: "/c python -c ""import sys,os; p=sys.executable; pw=p.replace('python.exe','pythonw.exe'); exe=pw if os.path.exists(pw) else p; f=open(os.path.join(sys.argv[1],'python_path.txt'),'w'); f.write(exe); f.close()"" ""{app}\bin"""; StatusMsg: "設定を記録中..."; Flags: waituntilterminated runhidden

; 必要なライブラリをpipでインストール
Filename: "cmd.exe"; Parameters: "/c python -m pip install flask werkzeug pillow numpy requests pytubefix fonttools proglog imageio-ffmpeg cryptography curl_cffi --quiet"; StatusMsg: "必要なライブラリをインストール中..."; Flags: waituntilterminated runhidden

; インストール完了後に起動するか聞く
Filename: "{sys}\wscript.exe"; Parameters: """{app}\launcher.vbs"""; Description: "今すぐ起動する"; Flags: nowait postinstall skipifsilent
