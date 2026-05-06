@echo off
:: FilmBill – Automatisches Datenbank-Backup
:: Speichert täglich eine Kopie der Datenbank in den Ordner "backups\"

set BACKUP_DIR=%~dp0backups
set DATE=%date:~6,4%-%date:~3,2%-%date:~0,2%
set TIME_STR=%time:~0,2%-%time:~3,2%
set FILENAME=filmbill_backup_%DATE%_%TIME_STR%.sql

:: Backup-Ordner erstellen falls nicht vorhanden
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

echo [%DATE% %TIME%] Starte Backup...

:: Datenbank sichern
docker exec filmbill_db pg_dump -U filmbill filmbill > "%BACKUP_DIR%\%FILENAME%"

if %errorlevel% == 0 (
    echo [OK] Backup gespeichert: backups\%FILENAME%
) else (
    echo [FEHLER] Backup fehlgeschlagen!
)

:: Alte Backups löschen (älter als 30 Tage)
forfiles /p "%BACKUP_DIR%" /s /m *.sql /d -30 /c "cmd /c del @path" 2>nul

echo Fertig. Drücke eine Taste zum Beenden...
pause
