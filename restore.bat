@echo off
:: FilmBill – Datenbank wiederherstellen
:: Verwendung: restore.bat backups\filmbill_backup_2024-01-01.sql

if "%1"=="" (
    echo Verwendung: restore.bat backups\dateiname.sql
    echo.
    echo Verfügbare Backups:
    dir /b backups\*.sql 2>nul
    pause
    exit /b 1
)

echo WARNUNG: Alle aktuellen Daten werden überschrieben!
set /p CONFIRM=Fortfahren? (J/N): 
if /i not "%CONFIRM%"=="J" exit /b 0

echo Stelle Datenbank wieder her aus: %1
docker exec -i filmbill_db psql -U filmbill filmbill < "%1"

if %errorlevel% == 0 (
    echo [OK] Wiederherstellung erfolgreich!
) else (
    echo [FEHLER] Wiederherstellung fehlgeschlagen!
)
pause
