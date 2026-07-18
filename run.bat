@echo off

set CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2
set PATH=%CUDA_PATH%\bin;%PATH%

echo CUDA:
nvcc --version

cd /d F:\AI_Bot

python main.py

pause