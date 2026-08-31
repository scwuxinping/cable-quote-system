@echo off
chcp 65001 >nul
cd /d %~dp0
echo 电缆报价系统启动中...
echo   本机访问:   http://127.0.0.1:8000
echo   局域网访问: http://本机IP:8000 （防火墙需放行 8000 端口）
py -3.13 manage.py runserver 0.0.0.0:8000
pause
