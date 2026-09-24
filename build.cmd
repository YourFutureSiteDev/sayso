@echo off
REM Rebuild dist\Sayso\Sayso.exe from source. Takes a few minutes.
cd /d "%~dp0"
uv run pyinstaller sayso.spec --noconfirm --clean
echo.
echo Built: "%~dp0dist\Sayso\Sayso.exe"
