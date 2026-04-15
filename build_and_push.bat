@echo off
setlocal
cd /d "C:\Users\kyohei\testsever"

rem ---------------------------------------------------------
rem  Argument parsing
rem  --build-only / -b : skip version bump and git push,
rem                      only run the Inno Setup build
rem ---------------------------------------------------------
set "BUILD_ONLY=0"
if /I "%~1"=="--build-only" set "BUILD_ONLY=1"
if /I "%~1"=="-b"           set "BUILD_ONLY=1"

rem ---------------------------------------------------------
rem  Auto-detect ISCC.exe (Inno Setup Compiler)
rem  Note: "(x86)" parens collide with `if` syntax, so we
rem        copy ProgramFiles(x86) into a separate variable.
rem ---------------------------------------------------------
set "PF=%ProgramFiles%"
set "PFX=%ProgramFiles(x86)%"
set "ISCC="

if exist "%PF%\Inno Setup 6\ISCC.exe"  set "ISCC=%PF%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%PFX%\Inno Setup 6\ISCC.exe" set "ISCC=%PFX%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%PF%\Inno Setup 5\ISCC.exe"  set "ISCC=%PF%\Inno Setup 5\ISCC.exe"
if not defined ISCC if exist "%PFX%\Inno Setup 5\ISCC.exe" set "ISCC=%PFX%\Inno Setup 5\ISCC.exe"
if not defined ISCC for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do if not defined ISCC set "ISCC=%%I"

if not defined ISCC (
    echo.
    echo ERROR: Inno Setup not found.
    echo   Install from: https://jrsoftware.org/isdl.php
    echo   Or add the folder containing ISCC.exe to PATH.
    echo.
    pause
    exit /b 1
)
echo Using ISCC: %ISCC%

if "%BUILD_ONLY%"=="1" goto BUILD

rem ---------------------------------------------------------
rem  Normal mode: bump version -> sync setup.iss -> push -> build
rem ---------------------------------------------------------
echo [1/5] version.json updating...
python -c "import json; f=open('version.json','r',encoding='utf-8'); d=json.load(f); f.close(); v=d['version'].split('.'); v[2]=str(int(v[2])+1); d['version']='.'.join(v); f=open('version.json','w',encoding='utf-8'); json.dump(d,f,ensure_ascii=False,indent=2); f.close(); print('new version: '+d['version'])"
if errorlevel 1 ( echo ERROR: version update failed & pause & exit /b 1 )

echo [2/5] Syncing setup.iss MyAppVersion with version.json...
python sync_setup_version.py
if errorlevel 1 ( echo ERROR: setup.iss sync failed & pause & exit /b 1 )

echo [3/5] GitHub push...
git remote set-url origin https://github.com/kyohei0612/clipgift.git
git add -A
git commit -m "update"
git push origin main
if errorlevel 1 ( echo ERROR: push failed & pause & exit /b 1 )

:BUILD
rem ---------------------------------------------------------
rem  Build installer
rem ---------------------------------------------------------
if "%BUILD_ONLY%"=="1" (
    echo [build-only mode] skipping version bump / setup.iss sync / push
    echo [1/1] Building installer...
) else (
    echo [4/5] Building installer...
)
"%ISCC%" "C:\Users\kyohei\testsever\setup.iss"
if errorlevel 1 ( echo ERROR: build failed & pause & exit /b 1 )

if "%BUILD_ONLY%"=="1" (
    echo [build-only mode] Done!
) else (
    echo [5/5] Done!
)
echo Output: C:\Users\kyohei\testsever\installer_output\YouTubeClipTool_Setup.exe
pause
endlocal
