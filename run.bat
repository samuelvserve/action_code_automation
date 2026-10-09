@echo off
rem Action Code Dashboard - Windows launcher
cd /d "%~dp0"
echo Starting Action Code Dashboard on http://127.0.0.1:5000
echo Default login: admin / admin123  (change it from Settings - Users)
python app.py
pause
