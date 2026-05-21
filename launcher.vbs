' ClipGift launcher.vbs (pywebview 版)
' Chrome --app 起動は launcher_window.py に移譲。Flask 起動 / ウィンドウ表示 / フォールバックを Python 側で実施。
' ASCII-only comments and CRLF line endings: VBScript engine requires CRLF.
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Dim appDir
appDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
WshShell.CurrentDirectory = appDir

' Read pythonw path from bin/python_path.txt
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

' Hand off to launcher_window.py.
' It owns: existing-server check, Flask subprocess spawn, pywebview window (Edge Chromium / WebView2),
' window-close -> Flask termination, and fallback to default browser if pywebview is unavailable.
WshShell.Run """" & pythonExe & """ """ & appDir & "\launcher_window.py""", 0, False