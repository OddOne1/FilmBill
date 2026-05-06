-- FilmBill Database Schema v3

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ─── Users ───────────────────────────────────────────────────────────────────
CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email         TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  password      TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin','user')),
  active        BOOLEAN NOT NULL DEFAULT true,
  reset_token   TEXT,
  reset_expires TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Settings ────────────────────────────────────────────────────────────────
CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);

INSERT INTO settings (key, value) VALUES
  ('company_name',        'Meine Produktionsfirma'),
  ('company_address',     'Musterstraße 1'),
  ('company_city',        '1010 Wien'),
  ('company_country',     'Österreich'),
  ('company_phone',       '+43 1 000 0000'),
  ('company_email',       'office@meinefirma.at'),
  ('company_website',     'https://meinefirma.at'),
  ('company_vat',         'ATU00000000'),
  ('company_bank',        ''),
  ('company_iban',        ''),
  ('company_bic',         ''),
  ('company_logo',        ''),
  ('currency_symbol',     '€'),
  ('currency_code',       'EUR'),
  ('currency_locale',     'de-AT'),
  ('default_tax_rate',    '0.20'),
  ('invoice_prefix',      'RE'),
  ('invoice_start',       '1000'),
  ('quote_prefix',        'AN'),
  ('quote_start',         '100'),
  ('invoice_terms',       'Zahlbar innerhalb von 30 Tagen netto.'),
  ('quote_terms',         'Dieses Angebot ist 30 Tage gültig.'),
  ('invoice_header',      ''),
  ('invoice_footer',      'Vielen Dank für Ihren Auftrag!'),
  ('invoice_intro',       'Wir erlauben uns, folgende Leistungen in Rechnung zu stellen:'),
  ('invoice_closing',     'Bei Fragen stehen wir Ihnen gerne zur Verfügung.'),
  ('quote_header',        ''),
  ('quote_footer',        'Wir freuen uns auf Ihre Auftragserteilung!'),
  ('quote_intro',         'Wir erlauben uns, folgendes Angebot zu unterbreiten:'),
  ('quote_closing',       'Dieses Angebot gilt vorbehaltlich Verfügbarkeit.'),
  ('agb_text',            ''),
  ('reminder_days',       '14'),
  ('reminder1_fee',       '10.00'),
  ('reminder2_fee',       '20.00'),
  ('reminder3_fee',       '30.00'),
  ('late_interest_rate',  '4'),
  ('production_fee_pct',  '0'),
  ('language',            'de'),
  ('tax_mode',            'per_item'),
  ('primary_color',       '#f5c842'),
  ('secondary_color',     '#4dabf7'),
  ('bg_color',            '#0d0d0d'),
  ('surface_color',       '#161616'),
  ('invoice_layout',      'classic');

-- ─── Inventory Categories ────────────────────────────────────────────────────
CREATE TABLE inventory_categories (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name       TEXT UNIQUE NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO inventory_categories (name, sort_order) VALUES
  ('Kamera', 1), ('Objektiv', 2), ('Beleuchtung', 3), ('Grip', 4),
  ('Audio', 5), ('Produktion', 6), ('Post', 7), ('Verbrauch', 8),
  ('Service', 9), ('Personal', 10), ('Sonstiges', 99);

-- ─── Companies ───────────────────────────────────────────────────────────────
CREATE TABLE companies (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name       TEXT NOT NULL,
  notes      TEXT,
  vat        TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Contact entries (emails/phones for companies and customers) ──────────────
CREATE TABLE contact_entries (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  entity_type TEXT NOT NULL CHECK (entity_type IN ('company','customer')),
  entity_id   UUID NOT NULL,
  type        TEXT NOT NULL CHECK (type IN ('email','phone','address')),
  label       TEXT NOT NULL DEFAULT 'Arbeit',
  value       TEXT NOT NULL,
  is_primary  BOOLEAN NOT NULL DEFAULT false,
  is_billing  BOOLEAN NOT NULL DEFAULT false,
  is_delivery BOOLEAN NOT NULL DEFAULT false,
  sort_order  INTEGER NOT NULL DEFAULT 0
);

-- ─── Customers ───────────────────────────────────────────────────────────────
CREATE TABLE customers (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  company_id  UUID REFERENCES companies(id) ON DELETE SET NULL,
  name        TEXT NOT NULL,
  notes       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Inventory ───────────────────────────────────────────────────────────────
CREATE TABLE inventory (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name        TEXT NOT NULL,
  sku         TEXT UNIQUE,
  category    TEXT NOT NULL DEFAULT 'Sonstiges',
  description TEXT,
  day_rate    NUMERIC(10,2) NOT NULL DEFAULT 0,
  week_rate   NUMERIC(10,2),
  sale_price  NUMERIC(10,2),
  tax_rate    NUMERIC(5,4) NOT NULL DEFAULT 0.20,
  unit        TEXT NOT NULL DEFAULT 'Tag',
  stock       INTEGER NOT NULL DEFAULT 1,
  active      BOOLEAN NOT NULL DEFAULT true,
  notes       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Text Templates ───────────────────────────────────────────────────────────
CREATE TABLE text_templates (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  type       TEXT NOT NULL CHECK (type IN ('header','intro','closing','footer','notes','agb')),
  name       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO text_templates (type, name, content) VALUES
  ('intro',   'Standard Rechnung DE', 'Wir erlauben uns, folgende Leistungen in Rechnung zu stellen:'),
  ('intro',   'Standard Angebot DE',  'Wir erlauben uns, folgendes Angebot zu unterbreiten:'),
  ('intro',   'Standard Invoice EN',  'We hereby invoice you for the following services:'),
  ('closing', 'Standard DE',          'Bei Fragen stehen wir Ihnen gerne zur Verfügung.'),
  ('closing', 'Standard EN',          'Please do not hesitate to contact us if you have any questions.'),
  ('footer',  'Standard DE',          'Vielen Dank für Ihren Auftrag!'),
  ('footer',  'Standard EN',          'Thank you for your business!'),
  ('agb',     'Standard AGB DE',      'Es gelten unsere Allgemeinen Geschäftsbedingungen. Gerichtsstand ist Wien. Änderungen und Irrtümer vorbehalten.');

-- ─── Documents ───────────────────────────────────────────────────────────────
CREATE TYPE doc_type   AS ENUM ('invoice','quote');
CREATE TYPE doc_status AS ENUM ('draft','sent','accepted','declined','partial','paid','overdue','cancelled');

CREATE TABLE invoices (
  id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  doc_no                TEXT UNIQUE NOT NULL,
  doc_type              doc_type NOT NULL DEFAULT 'invoice',
  doc_language          TEXT NOT NULL DEFAULT 'de',
  doc_layout            TEXT NOT NULL DEFAULT 'classic',
  customer_id           UUID NOT NULL REFERENCES customers(id),
  issued_by             UUID NOT NULL REFERENCES users(id),
  status                doc_status NOT NULL DEFAULT 'draft',
  subject               TEXT,
  issue_date            DATE NOT NULL DEFAULT CURRENT_DATE,
  due_date              DATE NOT NULL,
  valid_until           DATE,
  rental_start          DATE,
  rental_end            DATE,
  header_text           TEXT,
  intro_text            TEXT,
  closing_text          TEXT,
  footer_text           TEXT,
  notes_text            TEXT,
  agb_text              TEXT,
  include_agb           BOOLEAN NOT NULL DEFAULT false,
  subtotal              NUMERIC(12,2) NOT NULL DEFAULT 0,
  tax_total             NUMERIC(12,2) NOT NULL DEFAULT 0,
  discount_type         TEXT NOT NULL DEFAULT 'none',
  discount_value        NUMERIC(10,2) NOT NULL DEFAULT 0,
  discount_amount       NUMERIC(12,2) NOT NULL DEFAULT 0,
  production_fee_pct    NUMERIC(5,2) NOT NULL DEFAULT 0,
  production_fee_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
  manual_tax            NUMERIC(12,2),
  total                 NUMERIC(12,2) NOT NULL DEFAULT 0,
  amount_paid           NUMERIC(12,2) NOT NULL DEFAULT 0,
  terms                 TEXT,
  extra_recipients      TEXT,
  sent_at               TIMESTAMPTZ,
  paid_at               TIMESTAMPTZ,
  converted_from        UUID,
  reminder_level        INTEGER NOT NULL DEFAULT 0,
  last_reminder_at      TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Line Items ───────────────────────────────────────────────────────────────
CREATE TABLE invoice_items (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id   UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  inventory_id UUID REFERENCES inventory(id),
  description  TEXT NOT NULL,
  units        NUMERIC(10,2) NOT NULL DEFAULT 1,
  days         NUMERIC(10,2) NOT NULL DEFAULT 1,
  unit_price   NUMERIC(10,2) NOT NULL,
  tax_rate     NUMERIC(5,4) NOT NULL DEFAULT 0.20,
  line_total   NUMERIC(12,2) NOT NULL,
  sort_order   INTEGER NOT NULL DEFAULT 0
);

-- ─── Payments ────────────────────────────────────────────────────────────────
CREATE TABLE payments (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id  UUID NOT NULL REFERENCES invoices(id),
  amount      NUMERIC(12,2) NOT NULL,
  method      TEXT NOT NULL DEFAULT 'Überweisung',
  reference   TEXT,
  notes       TEXT,
  paid_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  recorded_by UUID REFERENCES users(id)
);

-- ─── Reminders ───────────────────────────────────────────────────────────────
CREATE TABLE reminders (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  invoice_id UUID NOT NULL REFERENCES invoices(id),
  level      INTEGER NOT NULL DEFAULT 0,
  fee        NUMERIC(10,2) NOT NULL DEFAULT 0,
  interest   NUMERIC(10,2) NOT NULL DEFAULT 0,
  sent_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sent_by    UUID REFERENCES users(id)
);

-- ─── Sequences ───────────────────────────────────────────────────────────────
CREATE SEQUENCE invoice_seq START 1000;
CREATE SEQUENCE quote_seq   START 100;

-- ─── Triggers ────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;
CREATE TRIGGER trg_users     BEFORE UPDATE ON users     FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_companies BEFORE UPDATE ON companies FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_customers BEFORE UPDATE ON customers FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_inventory BEFORE UPDATE ON inventory FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_invoices  BEFORE UPDATE ON invoices  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ─── Seed admin (password: Admin1234!) ───────────────────────────────────────
INSERT INTO users (email, name, password, role) VALUES (
  'admin@filmbill.local', 'System Admin',
  '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewdBPj4oVbMy2LCq', 'admin'
);

-- ─── Sample inventory ────────────────────────────────────────────────────────
INSERT INTO inventory (name, sku, category, day_rate, week_rate, stock, unit, tax_rate) VALUES
  ('ARRI ALEXA 35 Body',      'CAM-001', 'Kamera',     950.00, 4500.00, 2, 'Tag', 0.20),
  ('Sony FX9 Body',           'CAM-002', 'Kamera',     350.00, 1500.00, 3, 'Tag', 0.20),
  ('Canon EF 24-70mm f/2.8L', 'LNS-001', 'Objektiv',  120.00,  500.00, 5, 'Tag', 0.20),
  ('ARRI SkyPanel S60-C',     'LGT-001', 'Beleuchtung', 85.00,  350.00, 8, 'Tag', 0.20),
  ('Sound Devices 888',       'AUD-001', 'Audio',      150.00,  600.00, 3, 'Tag', 0.20),
  ('Kameraassistent',         'PER-001', 'Personal',   350.00, 1500.00,99, 'Tag', 0.20),
  ('Beleuchter',              'PER-002', 'Personal',   280.00, 1200.00,99, 'Tag', 0.20),
  ('DIT Station (komplett)',  'SVC-001', 'Service',    500.00, 2000.00, 1, 'Pausch.', 0.20);
