@echo off
setlocal
cd /d "%~dp0"
where /q py
if errorlevel 1 goto try_python
py -3 "%~dp0launch.py"
goto done

:try_python
where /q python
if errorlevel 1 goto missing_python
python "%~dp0launch.py"
goto done

:missing_python
echo Install Python 3.10 or newer from https://www.python.org/downloads/ and reopen this launcher.
pause
exit /b 1

:done
if errorlevel 1 pause
