@echo off
chcp 65001 >nul
title The Path to Ascension Begins with the Exam — Local Reader
cd /d "%~dp0"

echo ============================================================================
echo   《飞升从考试开始》 The Path to Ascension Begins with the Exam
echo   Building Glossary + Translated Chapters ^& Launching Local Reader...
echo ============================================================================
echo.

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set "PY_CMD=py"
) else (
    set "PY_CMD=python"
)

REM 1. Build site and generate reader_icon.ico first
%PY_CMD% "%~dp0build_reader.py"

REM 2. Automatically create an iconized Windows Shortcut (Launch Novel Reader.lnk) if not present
if not exist "%~dp0Launch Novel Reader.lnk" (
    if exist "%~dp0reader_icon.ico" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%~dp0Launch Novel Reader.lnk'); $s.TargetPath = '%~dp0Start_Reader.bat'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = '%~dp0reader_icon.ico,0'; $s.Description = 'Launch The Path to Ascension Begins with the Exam Reader'; $s.Save()" >nul 2>nul
    )
)

REM 3. Start the local progress-sync server and open the website
%PY_CMD% "%~dp0build_reader.py" --serve

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Reader exited with an error. Press any key to close...
    pause >nul
)
