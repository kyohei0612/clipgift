Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' スクリプトがあるフォルダを取得
Dim appDir
appDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
WshShell.CurrentDirectory = appDir

' python_path.txt からPythonパスを読む
Dim pythonExe
pythonExe = "pythonw"  ' デフォルト（PATHが通っている前提）

Dim txtPath
txtPath = appDir & "\bin\python_path.txt"
If fso.FileExists(txtPath) Then
    Dim ts
    Set ts = fso.OpenTextFile(txtPath, 1)
    Dim recorded
    recorded = Trim(ts.ReadAll())
    ts.Close
    ' python.exe → pythonw.exe に変換
    Dim pythonw
    pythonw = Replace(recorded, "python.exe", "pythonw.exe")
    If fso.FileExists(pythonw) Then
        pythonExe = pythonw
    ElseIf fso.FileExists(recorded) Then
        pythonExe = recorded
    End If
End If

WshShell.Environment("Process")("LAUNCHED_BY_VBS") = "1"
WshShell.Run """" & pythonExe & """ """ & appDir & "\app.py""", 0, False

' サーバー起動を少し待ってからブラウザを開く
WScript.Sleep 2000
WshShell.Run "http://127.0.0.1:5000", 1, False
