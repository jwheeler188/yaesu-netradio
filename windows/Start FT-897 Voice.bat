@echo off
rem W5PL FT-897D network radio - voice: audio client with your headset (Windows).
rem Uses the default playback and recording devices (Settings > System > Sound): pick your headset there first.
rem The web panel opens by itself; hold PTT (or Space) to talk.
rem Radio: use DIG mode (USER-U / USER-L) - the 897 only takes rear DATA-jack audio in DIG/PKT modes.
cd /d "%~dp0"
python -c "import pyaudio" 2>nul || python -m pip install --quiet pyaudio || (echo Python or pip not found - install Python 3 x64 from python.org & pause & exit /b 1)
python -u ..\client\ft897-audio.py --voice --open-panel %*
pause
