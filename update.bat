@echo off
:: FilmBill – Sicheres Update
:: Macht automatisch ein Backup VOR dem Update

echo ================================
echo   FilmBill – Sicheres Update
echo ================================
echo.

:: Backup zuerst
set BACKUP_DIR=%~dp0backups
set DATE=%date:~6,4%-%date:~3,2%-%date:~0,2%
set FILENAME=filmbill_vor_update_%DATE%.sql
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

echo [1/3] Erstelle Backup vor dem Update...
docker exec filmbill_db pg_dump -U filmbill filmbill > "%BACKUP_DIR%\%FILENAME%"
if %errorlevel% == 0 (
    echo [OK] Backup gespeichert: backups\%FILENAME%
) else (
    echo [WARNUNG] Backup fehlgeschlagen - trotzdem fortfahren?
    set /p CONFIRM=Fortfahren ohne Backup? (J/N): 
    if /i not "%CONFIRM%"=="J" exit /b 1
)

echo.
echo [2/3] Stoppe Container (Daten bleiben erhalten)...
docker compose down

echo.
echo [3/3] Baue und starte neu...
docker compose up -d --build

echo.
echo ================================
echo   Update abgeschlossen!
echo   Backup: backups\%FILENAME%
echo ================================
pause
