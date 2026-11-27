@echo off
rem W5PL FT-897D network radio - lets the web panel's "Start voice client" button start the voice client on this PC.
rem Registers the ft897voice:// link for this Windows user only (no administrator needed). The browser asks once.
rem Remove: reg delete HKCU\Software\Classes\ft897voice /f   (run this again after moving the folder)
set "BAT=%~dp0Start FT-897 Voice.bat"
reg add "HKCU\Software\Classes\ft897voice" /ve /d "URL:FT-897 voice client" /f >nul
reg add "HKCU\Software\Classes\ft897voice" /v "URL Protocol" /d "" /f >nul
reg add "HKCU\Software\Classes\ft897voice\shell\open\command" /ve /d "\"%SystemRoot%\System32\cmd.exe\" /c \"\"%BAT%\" link\"" /f >nul
if errorlevel 1 (echo Could not register the link. & pause & exit /b 1)
echo Done: the web panel's "Start voice client" button now starts the voice client on this PC.
pause
