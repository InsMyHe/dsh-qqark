@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
setlocal EnableExtensions

set "PY="
set "CAND=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%CAND%" set "PY=%CAND%"
set "CAND=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%CAND%" set "PY=%CAND%"
set "CAND=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PY if exist "%CAND%" set "PY=%CAND%"
set "CAND=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%CAND%" set "PY=%CAND%"
if not defined PY for %%P in (python.exe) do set "PY=%%~$PATH:P"
if not defined PY set "PY=python"

"%PY%" main.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
endlocal
