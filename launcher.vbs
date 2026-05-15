' ClipGift launcher.vbs
' ASCII-only comments and CRLF line endings: VBScript engine requires CRLF.
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Dim appDir
appDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
WshShell.CurrentDirectory = appDir

' Read Python path from bin/python_path.txt
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

' 2026-05-15: CLIPGEN_PORT changed to 5001 (port 5000 is used by Style-Bert-VITS2).
' config.py reads CLIPGEN_<name> prefixed env vars, so CLIPGEN_PORT is correct.
Dim serverPort, serverUrl
serverPort = 5001
serverUrl = "http://127.0.0.1:" & serverPort

' Skip spawning a second pythonw if a server is already responding on the port.
' Without this guard, a double click leaks a duplicate pythonw that dies from port conflict
' and the VBScript run becomes unstable.
Dim alreadyRunning
alreadyRunning = False
On Error Resume Next
Dim http
Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
If Err.Number = 0 Then
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
    ' Start server. Environment("Process") does not propagate to child processes,
    ' so we set env vars via cmd /c before launching python.
    WshShell.Environment("Process")("LAUNCHED_BY_VBS") = "1"
    WshShell.Run "cmd.exe /c set CLIPGEN_PORT=" & serverPort & "&& set LAUNCHED_BY_VBS=1&& """ & pythonExe & """ """ & appDir & "\app.py""", 0, False

    ' Wait for the server to come up
    WScript.Sleep 10000
End If

' Open browser via "cmd /c start" to bypass any flaky ShellExecute behavior under wscript.
WshShell.Run "cmd.exe /c start """" """ & serverUrl & """", 0, False