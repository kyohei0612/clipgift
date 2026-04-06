@echo off
cd /d "C:\Users\kyohei\testsever"

echo [1/4] version.json updating...
python -c "import json; f=open('version.json','r',encoding='utf-8'); d=json.load(f); f.close(); v=d['version'].split('.'); v[2]=str(int(v[2])+1); d['version']='.'.join(v); f=open('version.json','w',encoding='utf-8'); json.dump(d,f,ensure_ascii=False,indent=2); f.close(); print('new version: '+d['version'])"
if errorlevel 1 ( echo ERROR: version update failed & pause & exit /b 1 )

echo [2/4] GitHub push...
git remote set-url origin https://github.com/kyohei0612/clipgift.git
git add -A
git commit -m "update"
git push origin main
if errorlevel 1 ( echo ERROR: push failed & pause & exit /b 1 )

echo [3/4] Building installer...
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "C:\Users\kyohei\testsever\setup.iss"
if errorlevel 1 ( echo ERROR: build failed & pause & exit /b 1 )

echo [4/4] Done!
echo Output: C:\Users\kyohei\testsever\installer_output\YouTubeClipTool_Setup.exe
pause
