
::@echo off
 
::echo.
::echo === Starte cargo run ===
::cargo run --bin AI_Bot 2>$null



 :: cargo run --bin AI_Bot 2>&1

 ::Invoke-WebRequest -Uri "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q8_0.bin" -OutFile "ggml-large-v3-turbo-q8_0.bin"


@echo off

:: Rust zuerst starten
start "Rust" cargo run --release

:: Warten bis Rust bereit ist (ping als timer trick)
ping -n 4 127.0.0.1 > nul

:: Python starten
start "Python" python main.py

echo Beide Prozesse gestartet.
pause