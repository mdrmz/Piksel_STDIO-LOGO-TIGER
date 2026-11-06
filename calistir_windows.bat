@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Logo Tiger Analiz

where python >nul 2>nul
if errorlevel 1 (
  echo Python bulunamadi. https://www.python.org/downloads/ adresinden Python 3.11+ kurun
  echo (kurulumda "Add python.exe to PATH" kutusunu isaretleyin^).
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Ilk calistirma: sanal ortam kuruluyor...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 ( echo Paketler kurulamadi. Internet baglantisini kontrol edin. & pause & exit /b 1 )
)

if not exist "demo\logo_demo.sqlite" ".venv\Scripts\python.exe" demo\demo_db_olustur.py demo\logo_demo.sqlite

".venv\Scripts\python.exe" app.py
pause
