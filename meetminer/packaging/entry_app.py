"""Start-up script PyInstaller turns into MeetMiner.exe. All the code lives in the meetminer package."""
import sys

from meetminer.app import run

sys.exit(run())
