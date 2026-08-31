@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo   电缆报价系统 - 首次初始化
echo ============================================
py -3.13 -m pip install -r requirements.txt
py -3.13 manage.py migrate
py -3.13 manage.py seed
echo.
echo 初始化完成！双击 start.bat 启动系统。
pause
