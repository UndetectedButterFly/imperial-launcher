@echo off
REM Локальная сборка exe (нужен Python 3.12 для Windows: https://www.python.org/downloads/)
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :err
python -m unittest discover -s tests || goto :err
pyinstaller --noconfirm --onefile --windowed --name ImperialLauncher --icon assets\icon.ico --collect-all customtkinter --add-data "assets;assets" main.py || goto :err
echo.
echo Готово: dist\ImperialLauncher.exe
exit /b 0
:err
echo Сборка не удалась.
exit /b 1
