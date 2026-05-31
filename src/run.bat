
@echo off
 
echo === Erstelle data.bin ===
type nul > data.bin
echo data.bin erstellt
 
echo.
echo === Starte cargo run ===
cargo run
 
pause
 