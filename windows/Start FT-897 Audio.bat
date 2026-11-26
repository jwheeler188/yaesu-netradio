@echo off
rem W5PL FT-897D network radio - laptop audio client for Windows.
rem Needs: Python 3 from python.org (tick "Add python.exe to PATH") and VB-Audio Virtual Cable (vb-audio.com/Cable).
rem Windows on ARM (e.g. Surface Pro X / 11): install the x64 Python - PyAudio has no ARM64 package.
rem WSJT-X: Rig "Hamlib NET rigctl", Network Server <Pi address>:4533, PTT CAT,
rem         audio Input "CABLE Output", Output "CABLE Input".
rem The first start asks for the Pi's address (saved in server.txt). Another one:  "Start FT-897 Audio.bat" --server 192.168.1.50
rem Only one program at a time can use VB-CABLE.
cd /d "%~dp0"
python -c "import pyaudio" 2>nul || python -m pip install --quiet pyaudio || (echo Python or pip not found - install Python 3 x64 from python.org & pause & exit /b 1)
python -u ..\client\ft897-audio.py %*
pause
