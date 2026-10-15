@echo off
setlocal
title Nexora3 - Agente Windows (desenvolvimento)
set "NEXORA_DEV_BRANCH=fix/contas-geracao-senha-fluxo"
echo.
echo Atualizando e executando codigo de desenvolvimento do Nexora3...
echo Branch: %NEXORA_DEV_BRANCH%
echo Os perfis locais nao serao apagados.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/gustavo123123131/nexora3/fix/contas-geracao-senha-fluxo/instalar-agente-windows.ps1' -OutFile (Join-Path $env:TEMP 'instalar-agente-nexora-dev.ps1') -UseBasicParsing"
if errorlevel 1 (
  echo Falha ao atualizar o instalador de desenvolvimento.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\instalar-agente-nexora-dev.ps1" -Branch "%NEXORA_DEV_BRANCH%"
if errorlevel 1 (
  echo Erro ao iniciar o agente. Confira a mensagem acima.
  pause
  exit /b 1
)
pause
