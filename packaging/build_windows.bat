@echo off
setlocal

set ROOT=%~dp0..
cd /d %ROOT%

if not exist "%ROOT%\\.venv311" (
  py -3.11 -m venv "%ROOT%\\.venv311"
)

call "%ROOT%\\.venv311\\Scripts\\activate.bat"
python -m pip install --upgrade pip
pip install -r "%ROOT%\\requirements.txt"
pip install pyinstaller

pyinstaller -y packaging\\pyinstaller_app.spec
