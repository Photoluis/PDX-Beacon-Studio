# Third-party components

The Windows package (`PDX_Beacon_Studio_v*_Windows.zip`) contains:

| Component | Licence | Source |
|---|---|---|
| FFmpeg 9.0.2 "full" build by Gyan Doshi (`ffmpeg.exe`, `ffprobe.exe`) | GPL-3.0 (licence and build notes in `ffmpeg/LICENSE`, `ffmpeg/README.txt`) | https://ffmpeg.org/download.html · https://www.gyan.dev/ffmpeg/builds/ |
| Qt 6 / PySide6 6.12 | LGPL-3.0 | https://code.qt.io · https://download.qt.io/official_releases/QtForPython/ |
| NumPy | BSD-3-Clause | https://github.com/numpy/numpy |
| pySerial | BSD-3-Clause | https://github.com/pyserial/pyserial |
| Python 3 runtime | PSF License | https://www.python.org |
| PyInstaller bootloader | GPL-2.0 with bootloader exception | https://github.com/pyinstaller/pyinstaller |

The Qt libraries are shipped as separate DLLs and can be replaced by the user.
