@echo off
chcp 65001 >nul
cd /d %~dp0
echo 电缆报价系统（生产模式 waitress）启动中...
echo   本机访问:   http://127.0.0.1:8000
echo   局域网访问: http://本机IP:8000
echo   停止: 按 Ctrl+C
py -3.13 -m pip install -q waitress 2>nul
py -3.13 -m waitress --listen=0.0.0.0:8000 config.wsgi:application
pause
