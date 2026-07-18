@echo off
if "%1"=="" (
    echo Microsoft C/C++ Optimizing Compiler Version 19.43.34808
    exit /b 0
)

cl.exe %*