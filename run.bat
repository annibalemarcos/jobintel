@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
  echo [SETUP] Criando ambiente virtual...
  py -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt || goto :error
python crawler.py %*
goto :eof
:error
echo.
echo [ERRO] O programa terminou com erro.
pause
exit /b 1
