@echo off
cd /d %~dp0\..
python scripts\inspect_response_library.py outputs\H_E662_w050_080_MURA24m_customR_beta75.npz
pause
