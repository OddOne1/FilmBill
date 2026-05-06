# 🎬 FilmBill — Production & Equipment Rental Billing

A self-hosted Docker billing application for film production companies and equipment rentals.

## Features

- **Invoices** — Create, edit, send, track payment status
- **Inventory** — Full equipment catalog with day/week rates, SKUs, tax rates, stock levels
- **Customers** — Complete CRM with address, phone, email
- **Payments** — Record partial/full payments, payment history per invoice
- **Users** — Multi-user with role-based access (admin / staff), invite via MS365 email
- **Email** — Powered by your Microsoft 365 SMTP server (password resets, invoice delivery, user invites)
- **Dashboard** — Revenue summary, outstanding balances, overdue alerts

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/filmbill.git
cd filmbill

cp .env.example .env
nano .env        # fill in your values (see below)
```

### 2. Configure `.env`

| Variable | Description |
|---|---|
| `POSTGRES_PASSWORD` | Strong DB password |
| `JWT_SECRET` | 64-char random hex string |
| `SMTP_USER` | Your MS365 email address |
| `SMTP_PASS` | Your MS365 app password (not login password) |
| `SMTP_FROM` | Sender address |
| `COMPANY_NAME` | Shown on invoices and emails |
| `COMPANY_ADDRESS` | Your company address |
| `APP_URL` | Your server URL, e.g. `http://192.168.1.100` |

#### Generating a JWT secret:
```bash
node -e "console.log(require('crypto').randomBytes(64).toString('hex'))"
```

#### MS365 App Password:
Go to **Microsoft 365 Admin Center → Users → Active users → [your user] → Mail → Manage email apps** and enable SMTP AUTH. Then create an app password at https://account.microsoft.com/security.

### 3. Start

```bash
docker compose up -d
```

The app runs on **http://localhost** (or your `HTTP_PORT`).

### 4. First Login

```
Email:    admin@filmbill.local
Password: Admin1234!
```

**⚠️ Change this password immediately** via Users → Change My Password.

---

## Usage Guide

### Inventory
1. Go to **Inventory** → **Add Item**
2. Fill in name, SKU, category, day rate, week rate (optional), tax rate, stock
3. Items appear in the line-item picker when creating invoices

### Customers
1. Go to **Customers** → **Add Customer**
2. Fill in contact details, address, company

### Creating an Invoice
1. Go to **Invoices** → **New Invoice**
2. Select a customer, set due date and rental dates
3. Add line items — pick from inventory or type custom items
4. Set quantities and prices (auto-filled from inventory)
5. Save as draft or use **Send on Save** to email immediately

### Emailing Invoices
- Click **Email Invoice** on any sent/draft invoice
- The invoice summary is sent to the customer's email via your MS365 server
- Status automatically changes from `draft` → `sent`

### Recording Payments
- Open an invoice → **Record Payment**
- Enter amount, method, reference number
- Status updates automatically: `partial` or `paid`

### Inviting Team Members
1. Go to **Users** (admin only) → **Invite User**
2. Enter name, email, role
3. A temporary password is emailed to them via MS365

---

## Architecture

```
┌─────────────────────────────────────┐
│              Nginx :80              │  ← Single entry point
├──────────────────┬──────────────────┤
│   React SPA      │   Node.js API    │
│   (frontend:80)  │   (backend:4000) │
└──────────────────┴────────┬─────────┘
                            │
                   ┌────────▼────────┐
                   │  PostgreSQL 16  │
                   └─────────────────┘
```

All services run in Docker. Data is persisted in named volumes (`pgdata`, `uploads`).

---

## Backup

```bash
# Backup database
docker exec filmbill_db pg_dump -U filmbill filmbill > backup_$(date +%Y%m%d).sql

# Restore
docker exec -i filmbill_db psql -U filmbill filmbill < backup_20240101.sql
```

---

## Updating

```bash
git pull
docker compose up -d --build
```

---

## Pushing to GitHub

### If starting fresh:
```bash
cd filmbill
git init
git add .
git commit -m "Initial FilmBill setup"

# Create repo on GitHub (via browser or gh CLI)
gh repo create filmbill --private --source=. --push

# Or manually:
git remote add origin https://github.com/YOUR_USERNAME/filmbill.git
git branch -M main
git push -u origin main
```

### Using GitHub CLI with Google account:
```bash
# Install gh CLI if needed
brew install gh       # macOS
sudo apt install gh   # Ubuntu

# Authenticate (will open browser for Google/GitHub OAuth)
gh auth login

# Create and push
gh repo create filmbill --private --source=. --push --description "FilmBill - Production Billing"
```

---

## Ports & Security

- The app runs on port 80 by default (`HTTP_PORT` in `.env`)
- For HTTPS/SSL, place a reverse proxy (Caddy, Traefik, or nginx with certbot) in front
- Never expose `POSTGRES_PASSWORD` or `JWT_SECRET` in version control — `.env` is gitignored

---

## Support

Default admin credentials are seeded on first run. The bcrypt hash in `init.sql` is for `Admin1234!`.
