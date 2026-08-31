@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo   电缆报价系统 - 注册每日自动任务
echo ============================================
echo 将注册两个 Windows 任务计划：
echo   1. CableQuoteBackup  每日 07:00 自动备份数据库
echo   2. CableQuoteCuPrice 每日 07:30 自动抓取铜价
echo.
set BASE=%~dp0
schtasks /Create /TN "CableQuoteBackup" /TR "\"%BASE%backup.bat\"" /SC DAILY /ST 07:00 /F
schtasks /Create /TN "CableQuoteCuPrice" /TR "cmd /c cd /d \"%BASE%\" && py -3.13 manage.py fetch_cu_price >> \"%BASE%cu_price.log\" 2>&1" /SC DAILY /ST 07:30 /F
echo.
echo 完成！可在"任务计划程序"中查看或调整时间。
echo 注意：任务计划以当前用户运行，需保证届时电脑开机；备份会弹窗提示，可忽略。
pause
