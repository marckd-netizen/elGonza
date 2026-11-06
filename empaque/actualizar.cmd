@echo off
setlocal
rem Actualiza elGonza en la carpeta de la red copiando solo lo que cambia entre versiones (elGonza.exe y sus datos),
rem en vez de los ~1400 archivos del paquete completo. Si esta versión trae otras librerías, avisa y no copia nada.
set "AQUI=%~dp0"
set "DESTINO=%~1"
if "%DESTINO%"=="" set /p "DESTINO=Carpeta de elGonza en la red (la que tiene elGonza.cmd y programa): "
set "DESTINO=%DESTINO:"=%"
if not exist "%DESTINO%\programa\elGonza.exe" (
  echo No encuentro "%DESTINO%\programa\elGonza.exe".
  pause
  exit /b 1
)
fc /b "%AQUI%programa\dependencias.txt" "%DESTINO%\programa\dependencias.txt" >nul 2>&1
if errorlevel 1 (
  echo.
  echo Esta version trae librerias distintas a las instaladas en "%DESTINO%".
  echo Por esta vez hay que copiar el paquete completo ^(elGonza-windows^). No se copio nada.
  pause
  exit /b 1
)
copy /y "%AQUI%programa\elGonza.exe" "%DESTINO%\programa\elGonza.exe" >nul || goto error
robocopy "%AQUI%programa\_internal\elgonza" "%DESTINO%\programa\_internal\elgonza" /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto error
for %%f in (elGonza.cmd actualizar.cmd LEEME.md libro_de_criterios.md) do copy /y "%AQUI%%%f" "%DESTINO%\%%f" >nul
echo elGonza actualizado en "%DESTINO%".
pause
exit /b 0
:error
echo No pude copiar: ¿hay alguna computadora con elGonza abierto desde la red? Cerralo y volve a probar.
pause
exit /b 1
