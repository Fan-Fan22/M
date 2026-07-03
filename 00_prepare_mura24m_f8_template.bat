@echo off
cd /d %~dp0\..
python scripts\prepare_mura24m_f8_template.py --input templates\MURA24m_original.i --output templates\MURA24m_f8_E662_w050_080.i --energy-edges 0.50 0.80
pause
