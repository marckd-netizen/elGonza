@echo off
rem Abre elGonza desde la red sin la demora: copia el programa a esta computadora
rem solo cuando cambió (robocopy compara fecha y tamaño) y lo ejecuta desde acá.
set "ORIGEN=%~dp0programa"
set "LOCAL=%LOCALAPPDATA%\elGonza\programa"
if not exist "%ORIGEN%\elGonza.exe" (
  echo No encuentro "%ORIGEN%\elGonza.exe".
  pause
  exit /b 1
)
robocopy "%ORIGEN%" "%LOCAL%" /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >nul
if not exist "%LOCAL%\elGonza.exe" (
  rem si no se pudo copiar, se abre directo desde la red
  start "" "%ORIGEN%\elGonza.exe" %*
  exit /b 0
)
start "" "%LOCAL%\elGonza.exe" %*
