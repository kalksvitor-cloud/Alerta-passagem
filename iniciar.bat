@echo off
REM Carrega as chaves do arquivo .env e roda o comando. Ex.: iniciar.bat bot
cd /d "%~dp0"
for /f "usebackq tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
python passagens.py %*
