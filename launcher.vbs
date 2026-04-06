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

' サーバー起動
WshShell.Environment("Process")("LAUNCHED_BY_VBS") = "1"
WshShell.Run """" & pythonExe & """ """ & appDir & "\app.py""", 0, False

' サーバー起動を待つ（10秒）
WScript.Sleep 10000

' ブラウザを開く
WshShell.Run "http://127.0.0.1:5000", 1, False
