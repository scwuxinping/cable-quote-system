@echo off
cd /d %~dp0
echo Starting cable quote system at http://127.0.0.1:8000
echo LAN address: use this computer's IP, e.g. http://192.168.x.x:8000
py -3.13 manage.py runserver 0.0.0.0:8000
pause
