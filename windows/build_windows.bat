@echo off
REM ===== Build QBasicIDE.exe and the Windows installer (QBasicIDE_Setup.exe) =====
REM Needs: Python 3.9+ (python.org installer, with tcl/tk)  and  Inno Setup 6 (https://jrsoftware.org/isdl.php)
cd /d "%~dp0\.."
python -m pip install --upgrade pip pyinstaller pillow || goto :err
python -m PyInstaller --noconfirm --clean --onefile --windowed --name QBasicIDE --hidden-import PIL.ImageDraw --hidden-import PIL.ImageFont qbasic_ide.py || goto :err
echo.
echo EXE built:  dist\QBasicIDE.exe
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
  "%ISCC%" windows\QBasicIDE.iss || goto :err
  echo.
  echo INSTALLER built:  installer\QBasicIDE_Setup.exe
) else (
  echo Inno Setup 6 not found - install it, then run:  ISCC windows\QBasicIDE.iss
)
goto :eof
:err
echo BUILD FAILED
exit /b 1
