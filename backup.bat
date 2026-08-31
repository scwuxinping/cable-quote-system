@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist db.sqlite3 (
    echo 未找到 db.sqlite3，请先运行 init.bat 初始化。
    pause
    exit /b 1
)
if not exist backups mkdir backups
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
copy /y db.sqlite3 "backups\db_%TS%.sqlite3" >nul
powershell -NoProfile -Command "Get-ChildItem 'backups\db_*.sqlite3' | Sort-Object Name -Descending | Select-Object -Skip 30 | Remove-Item -Force" >nul 2>&1
echo 备份完成: backups\db_%TS%.sqlite3 （自动保留最近 30 份）
echo 建议加入 Windows 任务计划每日执行一次。
pause
