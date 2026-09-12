@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\admin\AppData\Local\Temp\lab-booking-install-81b9c7a8\scripts\start-demo.ps1" -Password "R9-Sec2-99" -Port 8123 > "C:\Users\admin\Desktop\????\docs\evidence\install\install_start.log" 2>&1
exit /b %ERRORLEVEL%
