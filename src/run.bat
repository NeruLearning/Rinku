
@echo off
 
echo.
echo === Starte cargo run ===
cargo run --bin AI_Bot 2>$null



 :: cargo run --bin AI_Bot 2>&1

 ::Invoke-WebRequest -Uri "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q8_0.bin" -OutFile "ggml-large-v3-turbo-q8_0.bin"