# 🐳 FilmBill auf TrueNAS SCALE 25.10 "Goldeye"

Komplette Anleitung für TrueNAS SCALE Custom App mit externem HTTPS-Zugriff über `filmbills.yon.studio`.

---

## 📋 Übersicht

```
Internet (filmbills.yon.studio HTTPS)
        ↓
   Router/Firewall (Port 443 → TrueNAS)
        ↓
   TrueNAS Reverse Proxy (Cloudflare/Caddy)
        ↓
   FilmBill Container (Port 8080)
        ↓
   PostgreSQL DB
```

---

## 🚀 Schritt 1: Datasets vorbereiten

In TrueNAS Web-UI:

1. **Datasets** → wähle Pool **HDDs-1**
2. **Add Dataset** → Name: `Docker` (falls noch nicht da)
3. Im Docker-Dataset: **Add Dataset** → Name: `filmbill`
4. Im filmbill-Dataset diese Sub-Datasets erstellen:
   - `postgres`
   - `uploads`

Alternativ via SSH (schneller):
```bash
sudo mkdir -p /mnt/HDDs-1/Docker/filmbill/{postgres,uploads}
sudo chown -R 999:999 /mnt/HDDs-1/Docker/filmbill/postgres
sudo chmod -R 777 /mnt/HDDs-1/Docker/filmbill
```

---

## 🚀 Schritt 2: Konfigurationsdateien hochladen

Zwei Dateien müssen auf den Server:

### a) `init.sql` (Datenbank-Schema)

Per SSH:
```bash
sudo nano /mnt/HDDs-1/Docker/filmbill/init.sql
```

Den **kompletten Inhalt** aus der Datei `backend/init.sql` einfügen (auf GitHub) → Strg+O, Enter, Strg+X

### b) `nginx.conf` (Reverse Proxy intern)

```bash
sudo nano /mnt/HDDs-1/Docker/filmbill/nginx.conf
```

Inhalt einfügen:
```nginx
server {
  listen 80;
  client_max_body_size 10M;

  location /api/ {
    proxy_pass         http://backend:4000/api/;
    proxy_http_version 1.1;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Real-IP         $remote_addr;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
  }

  location / {
    proxy_pass         http://frontend:80/;
    proxy_http_version 1.1;
    proxy_set_header   Host $host;
  }
}
```

Speichern: Strg+O, Enter, Strg+X

---

## 🚀 Schritt 3: Custom App in TrueNAS anlegen

1. **Apps** → **Discover Apps** → oben rechts **Custom App**
2. **Application Name:** `filmbill`
3. **Custom Compose:** Den Inhalt von `truenas/docker-compose-truenas.yml` reinkopieren
4. **WICHTIG: Diese Werte direkt im YAML ändern:**
   - `POSTGRES_PASSWORD` → starkes Passwort (an 2 Stellen, müssen identisch sein!)
   - `JWT_SECRET` → langer Zufallsstring (mindestens 32 Zeichen)
   - `SMTP_PASS` → dein M365 Passwort
5. **Install** klicken

---

## 🚀 Schritt 4: Erster Start prüfen

Nach 1-2 Minuten:
- **Apps** → **Installed Apps** → **filmbill** sollte **Running** anzeigen
- Im Browser testen: **http://192.168.1.100:8080**
- Login: `admin@filmbill.local` / `Admin1234!`

---

## 🚀 Schritt 5: Admin-Passwort setzen

Wegen bcrypt-Hash müssen wir das Passwort einmal manuell setzen:

```bash
# Container-Name finden:
sudo docker ps | grep filmbill_api

# Hash generieren:
sudo docker exec -it ix-filmbill-backend-1 node -e "const b=require('bcrypt');b.hash('DEIN_NEUES_PASSWORT',12).then(h=>console.log(h))"

# In Datenbank setzen:
sudo docker exec -it ix-filmbill-db-1 psql -U filmbill -d filmbill
```
Dann im psql Prompt:
```sql
UPDATE users SET password='HASH_VON_OBEN' WHERE email='admin@filmbill.local';
\q
```

> ℹ️ TrueNAS benennt Container mit Prefix `ix-filmbill-` — falls die Namen anders sind, mit `docker ps` prüfen.

---

## 🌐 Schritt 6: Externer Zugriff über filmbills.yon.studio

Du hast mehrere Möglichkeiten — die einfachste ist **Cloudflare Tunnel** (kein offener Port, kostenlos, automatisches HTTPS).

### Option A: Cloudflare Tunnel (empfohlen) ⭐

1. **Cloudflare Account** mit der Domain `yon.studio` haben
2. **Cloudflare Zero Trust Dashboard** → **Networks** → **Tunnels** → **Create a tunnel**
3. Name: `filmbill-truenas`
4. Token kopieren
5. In TrueNAS eine **zweite Custom App** anlegen mit:
```yaml
services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: cloudflared
    restart: unless-stopped
    command: tunnel --no-autoupdate run --token DEIN_TOKEN_HIER
```
6. Im Cloudflare Tunnel-Dashboard:
   - **Public Hostname** hinzufügen
   - **Subdomain:** `filmbills`
   - **Domain:** `yon.studio`
   - **Service Type:** `HTTP`
   - **URL:** `192.168.1.100:8080`

✅ Fertig! `https://filmbills.yon.studio` funktioniert mit automatischem HTTPS, ohne offene Ports.

### Option B: TrueNAS mit Caddy-Reverse-Proxy + DNS

Falls du direkt einen Port im Router öffnen willst:
1. Port-Forwarding im Router: `443 → 192.168.1.100:443`
2. DNS-Eintrag bei deinem Provider: `filmbills.yon.studio → IP_DEINES_ROUTERS`
3. Auf TrueNAS einen Caddy-Container anlegen für Auto-HTTPS via Let's Encrypt

> Option A ist viel einfacher und sicherer — Cloudflare empfehle ich dringend.

---

## 🔄 Updates einspielen

Wenn ich neue FilmBill-Versionen baue, einfach:

1. **Apps** → **filmbill** → **Edit**
2. **Update** klicken (lädt `:latest` neu)
3. Oder per SSH:
```bash
sudo docker compose -p ix-filmbill pull
sudo docker compose -p ix-filmbill up -d
```

---

## 💾 Backup

Datensicherung des Postgres-Datasets in TrueNAS einfach über **Snapshots** auf Dataset-Ebene:

**Datasets** → `HDDs-1/Docker/filmbill/postgres` → **Periodic Snapshot Tasks** anlegen.

Empfehlung: Tägliche Snapshots, 30 Tage Aufbewahrung.

---

## 🆘 Troubleshooting

**"port already allocated"**
→ Port 8080 belegt? In Compose ändern: `"8081:80"`

**"no matching manifest for linux/arm64"**
→ Falsche CPU-Architektur. Auf x86_64 (Intel/AMD) Server kein Problem.

**"image not found"**
→ Sicherstellen dass Docker Hub Image existiert:
https://hub.docker.com/r/oddoneeu/filmbill-backend

**Container startet nicht**
→ Logs prüfen: **Apps** → filmbill → **Logs** Tab
