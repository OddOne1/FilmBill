@echo off
:: FilmBill – Schnelles Deployment von Docker Hub
:: Lädt die neuesten Images und startet sie neu (kein Build nötig!)

echo ================================
echo  FilmBill - Deploy from Docker Hub
echo ================================
echo.

:: Backup zuerst
set BACKUP_DIR=%~dp0backups
set DATE=%date:~6,4%-%date:~3,2%-%date:~0,2%
set FILENAME=filmbill_vor_deploy_%DATE%.sql
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

echo [1/3] Backup vor Deployment...
docker exec filmbill_db pg_dump -U filmbill filmbill > "%BACKUP_DIR%\%FILENAME%" 2>nul

echo.
echo [2/3] Lade neueste Images von Docker Hub...
docker compose -f docker-compose.prod.yml pull

echo.
echo [3/3] Starte Container neu...
docker compose -f docker-compose.prod.yml up -d

echo.
echo ================================
echo  Deployment abgeschlossen!
echo  Backup: backups\%FILENAME%
echo ================================
pause
