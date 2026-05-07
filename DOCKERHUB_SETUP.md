# 🚀 Docker Hub + GitHub Actions Setup

Mit dieser Konfiguration wird FilmBill **automatisch gebaut** und auf Docker Hub gehostet, sobald du Code zu GitHub pushst. Server brauchen dann nur 30 Sekunden für ein Update statt 5 Minuten!

---

## Einmaliges Setup (5 Minuten)

### 1. Docker Hub Account
1. Geh zu **https://hub.docker.com** und erstelle einen kostenlosen Account
2. Username merken (z.B. `oddone1`)

### 2. Docker Hub Access Token erstellen
1. Auf Docker Hub einloggen
2. Klick auf dein Profil oben rechts → **Account Settings**
3. Links **Personal access tokens** → **Generate new token**
4. Name: `github-actions-filmbill`
5. Permissions: **Read, Write, Delete**
6. **Generate** → Token kopieren (wird nur einmal angezeigt!)

### 3. GitHub Secrets hinzufügen
1. Geh zu deinem GitHub Repo: **https://github.com/OddOne1/FilmBill**
2. **Settings** → **Secrets and variables** → **Actions**
3. **New repository secret** → zwei Secrets erstellen:

| Name | Wert |
|---|---|
| `DOCKERHUB_USERNAME` | dein Docker Hub Username (z.B. `oddone1`) |
| `DOCKERHUB_TOKEN` | der Token von Schritt 2 |

### 4. Code zu GitHub pushen
```powershell
cd C:\Users\msonn\Downloads\filmbill
git add .
git commit -m "Add Docker Hub workflow"
git push
```

---

## Was passiert jetzt automatisch?

Bei jedem `git push` zu GitHub:
1. **GitHub Actions startet automatisch** (siehst du im **Actions** Tab auf GitHub)
2. **Backend & Frontend Images werden gebaut**
3. **Auf Docker Hub hochgeladen** als:
   - `dein-username/filmbill-backend:latest`
   - `dein-username/filmbill-frontend:latest`
4. Build dauert ca. **3-5 Minuten** (passiert im Hintergrund)

---

## Auf deinem Server / PC verwenden

### Option A: Mit selber Bauen (alte Methode)
```powershell
docker compose up -d --build
```
Bauzeit: ~5 Minuten

### Option B: Von Docker Hub laden (neu, schneller!)
```powershell
docker compose -f docker-compose.prod.yml up -d
```
Bauzeit: ~30 Sekunden ✅

### Schnelles Update mit deploy.bat:
Doppelklick auf **`deploy.bat`** — macht automatisch:
1. Backup erstellen
2. Neue Images von Docker Hub holen
3. Container neu starten

---

## Workflow zusammengefasst

```
1. Code ändern in VS Code
2. git push
3. ☕ Kaffee holen (3-5 Min Build-Zeit auf GitHub)
4. Auf Server: deploy.bat doppelklicken (30 Sekunden)
5. Fertig!
```

---

## Auf einem fremden Server installieren

Falls du FilmBill auf einem komplett neuen Rechner aufsetzen willst:

```powershell
# Nur 4 Dateien werden gebraucht — kein Code!
mkdir filmbill && cd filmbill

# Diese 4 Dateien herunterladen:
# - docker-compose.prod.yml
# - .env.example (zu .env umbenennen und ausfüllen)
# - backend/init.sql
# - nginx/default.conf

docker compose -f docker-compose.prod.yml up -d
```

**Vorteil:** Keine Source-Files, keine Builds, keine npm install — einfach Images runterladen und starten!

---

## Troubleshooting

### "denied: requested access to the resource is denied"
→ DOCKERHUB_USERNAME oder DOCKERHUB_TOKEN in GitHub Secrets ist falsch

### Workflow startet nicht
→ Prüfe dass die Datei `.github/workflows/docker-publish.yml` heißt und gepusht wurde

### "image not found" beim Pull
→ Erst Build auf GitHub abwarten (Actions Tab) bevor du `pull` ausführst

---

## Status prüfen

- **Build-Status auf GitHub:** https://github.com/OddOne1/FilmBill/actions
- **Deine Images auf Docker Hub:** https://hub.docker.com/u/DEIN-USERNAME
