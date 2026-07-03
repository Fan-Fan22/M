@echo off
cd /d %~dp0\..
python scripts\build_response_library_f8.py configs\config_MURA24m_E662_w050_080_customR_beta75.json --parse-only
pause
