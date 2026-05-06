@echo off
rem Windows のアイコンキャッシュを強制クリアする
rem 新しい ICO が反映されない時に使う（再インストール不要の代替手段）
rem 実行後、デスクトップ / スタートメニューのアイコンが新ロゴに更新される

echo === Windows IconCache をクリアします ===
echo.

rem IE4UInit でアイコンキャッシュ再構築フラグを立てる
ie4uinit.exe -ClearIconCache 2>nul
ie4uinit.exe -show 2>nul

rem Explorer を一時停止
echo Explorer を一時停止...
taskkill /IM explorer.exe /F >nul 2>&1

rem キャッシュファイル削除
echo キャッシュファイル削除中...
del /A /Q "%localappdata%\IconCache.db" 2>nul
del /A /F /Q "%localappdata%\Microsoft\Windows\Explorer\iconcache_*" 2>nul
del /A /F /Q "%localappdata%\Microsoft\Windows\Explorer\thumbcache_*" 2>nul

rem Explorer を再起動
echo Explorer を再起動...
start explorer.exe

echo.
echo === 完了 ===
echo デスクトップ・スタートメニューの ClipGift アイコンが新ロゴに更新されました。
echo 反映されない場合は一度サインアウト → サインインしてください。
echo.
pause
