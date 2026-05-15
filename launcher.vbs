Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' スクリプトがあるフォルダを取得
Dim appDir
appDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
WshShell.CurrentDirectory = appDir

' python_path.txt からPythonパスを読む
Dim pythonExe
pythonExe = "pythonw"

Dim txtPath
txtPath = appDir & "\bin\python_path.txt"
If fso.FileExists(txtPath) Then
    Dim ts
    Set ts = fso.OpenTextFile(txtPath, 1)
    Dim recorded
    recorded = Trim(ts.ReadAll())
    ts.Close
    Dim pythonw
    pythonw = Replace(recorded, "python.exe", "pythonw.exe")
    If fso.FileExists(pythonw) Then
        pythonExe = pythonw
    ElseIf fso.FileExists(recorded) Then
        pythonExe = recorded
    End If
End If

' 2026-05-15: CLIPGEN_PORT を 5001 に変更（port 5000 は Style-Bert-VITS2 が使用、衝突回避）
' config.py の _env_int は CLIPGEN_<name> プレフィックスを読むので CLIPGEN_PORT が正解
Dim serverPort, serverUrl
serverPort = 5001
serverUrl = "http://127.0.0.1:" & serverPort

' 既にサーバーが LISTENING かつ応答するなら、新規起動はスキップしてブラウザだけ開く
' 二重起動するとポート衝突で 2 つ目の pythonw が無音で落ち、VBS の挙動が不安定になる
Dim alreadyRunning
alreadyRunning = False
On Error Resume Next
Dim http
Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
If Err.Number = 0 Then
    ' SetTimeouts(resolve, connect, send, receive) はミリ秒
    http.SetTimeouts 500, 500, 500, 1500
    http.Open "GET", serverUrl & "/", False
    http.Send
    If Err.Number = 0 Then
        alreadyRunning = True
    End If
End If
Err.Clear
On Error Goto 0

If Not alreadyRunning Then
    ' サーバー起動
    ' VBS の Environment("Process") は子プロセスに伝わらないので cmd /c で env 設定後 python 起動
    WshShell.Environment("Process")("LAUNCHED_BY_VBS") = "1"
    WshShell.Run "cmd.exe /c set CLIPGEN_PORT=" & serverPort & "&& set LAUNCHED_BY_VBS=1&& """ & pythonExe & """ """ & appDir & "\app.py""", 0, False

    ' サーバー起動を待つ（10秒）
    WScript.Sleep 10000
End If

' ブラウザを開く
WshShell.Run serverUrl, 1, False
