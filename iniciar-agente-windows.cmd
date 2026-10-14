@echo off
setlocal
title Lixo CPA - Instalador do agente Windows
echo.
echo Baixando o instalador do repositorio gustavo123123131/nexora3...
echo O Chromium sera executado neste computador, nao no GitHub.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/gustavo123123131/nexora3/main/instalar-agente-windows.ps1' -OutFile (Join-Path $env:TEMP 'instalar-agente-windows-lixocpa.ps1') -UseBasicParsing"
if errorlevel 1 (
  echo Falha ao baixar o instalador. Verifique a internet.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\instalar-agente-windows-lixocpa.ps1"
if errorlevel 1 (
  echo O agente nao pode ser iniciado. Leia o erro acima.
  pause
  exit /b 1
)
pause
