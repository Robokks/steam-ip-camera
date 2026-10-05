@echo off
REM Build IPCameraRecorder.exe (output: dist\IPCameraRecorder.exe)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name IPCameraRecorder camera_app.py
echo.
echo Done. Your app is in: dist\IPCameraRecorder.exe
pause
