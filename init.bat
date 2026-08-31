@echo off
cd /d %~dp0
echo === Cable Quote System - Init ===
py -3.13 -m pip install django openpyxl
py -3.13 manage.py migrate
py -3.13 manage.py seed
echo Done. Run start.bat to launch.
pause
