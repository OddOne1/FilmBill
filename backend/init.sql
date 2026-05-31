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
  ('company_court',       ''),
  ('company_fn',          ''),
  ('company_tax_no',      ''),
  ('company_bank',        ''),
  ('company_iban',        ''),
  ('company_bic',         ''),
  ('company_logo',        ''),
  ('signature_name',      ''),
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
  ('text_color',          'auto'),
  ('text_dim_color',      'auto'),
  ('app_font',            'DM Sans'),
  ('doc_font',            'DM Sans'),
  ('custom_font_url',     ''),
  ('invoice_layout',      'yon-studio');

-- ─── Document Layouts ────────────────────────────────────────────────────────
CREATE TABLE doc_layouts (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  key         TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  description TEXT,
  html        TEXT NOT NULL,
  css         TEXT NOT NULL,
  is_default  BOOLEAN NOT NULL DEFAULT false,
  is_builtin  BOOLEAN NOT NULL DEFAULT false,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO doc_layouts (key, name, description, html, css, is_default, is_builtin) VALUES
('classic', 'Klassisch', 'Traditionelles Layout mit Logo oben links',
'<div class="doc">
  <header class="doc-head">
    {{#if logo}}<img src="{{logo}}" class="doc-logo">{{/if}}
    <div class="doc-company">
      <h1>{{company_name}}</h1>
      <p>{{company_address}}<br>{{company_city}}<br>{{company_country}}</p>
      <p>{{company_phone}} · {{company_email}}</p>
    </div>
  </header>
  <h2 class="doc-title">{{doc_label}} {{doc_no}}</h2>
  <div class="doc-meta">
    <div class="doc-customer">
      <strong>{{customer_name}}</strong><br>
      {{customer_address}}
    </div>
    <div class="doc-info">
      <p><strong>Datum:</strong> {{issue_date}}</p>
      <p><strong>Fällig:</strong> {{due_date}}</p>
    </div>
  </div>
  {{#if subject}}<p class="doc-subject">{{subject}}</p>{{/if}}
  {{#if intro_text}}<p>{{intro_text}}</p>{{/if}}
  <table class="doc-items">
    <thead><tr><th>Pos</th><th>Beschreibung</th><th>Anz.</th><th>Tage</th><th>Preis</th><th>Summe</th></tr></thead>
    <tbody>{{#each items}}<tr><td>{{@index}}</td><td>{{description}}</td><td>{{units}}</td><td>{{days}}</td><td>{{unit_price}}</td><td>{{line_total}}</td></tr>{{/each}}</tbody>
  </table>
  <div class="doc-totals">
    <div>Netto: {{subtotal}}</div>
    <div>MwSt: {{tax_total}}</div>
    <div class="doc-grand">Gesamt: {{total}}</div>
  </div>
  {{#if closing_text}}<p>{{closing_text}}</p>{{/if}}
  {{#if include_agb}}<div class="doc-agb"><h3>AGB</h3>{{agb_text}}</div>{{/if}}
  <footer>{{footer_text}}<br>{{terms}}</footer>
</div>',
'.doc{font-family:var(--doc-font);color:#1a1a1a;padding:40px}
.doc-head{display:flex;justify-content:space-between;margin-bottom:30px;border-bottom:2px solid #1a1a1a;padding-bottom:20px}
.doc-logo{max-height:60px;max-width:200px}
.doc-company h1{font-size:18px;margin:0}
.doc-company p{font-size:11px;color:#666;margin:4px 0}
.doc-title{font-size:24px;margin:30px 0 20px;text-transform:uppercase;letter-spacing:2px}
.doc-meta{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}
.doc-subject{font-weight:bold;margin:16px 0;font-size:14px}
.doc-items{width:100%;border-collapse:collapse;margin:20px 0}
.doc-items th{background:#f0f0f0;padding:8px;text-align:left;font-size:11px;text-transform:uppercase;border-bottom:2px solid #1a1a1a}
.doc-items td{padding:8px;border-bottom:1px solid #e5e5e5;font-size:12px}
.doc-totals{text-align:right;margin:20px 0}
.doc-totals div{margin:4px 0}
.doc-grand{font-size:18px;font-weight:bold;border-top:2px solid #1a1a1a;padding-top:8px;margin-top:8px}
.doc-agb{margin-top:30px;padding-top:20px;border-top:1px solid #ccc;font-size:10px;color:#666}
.doc-agb h3{font-size:12px;margin-bottom:8px}
.doc footer{margin-top:40px;padding-top:20px;border-top:1px solid #ccc;font-size:11px;color:#666;text-align:center}', false, true),

('modern', 'Modern', 'Modernes Layout mit farbigem Header',
'<div class="doc">
  <header class="doc-head">
    {{#if logo}}<img src="{{logo}}" class="doc-logo">{{/if}}
    <div><h1>{{doc_label}}</h1><p>{{doc_no}}</p></div>
  </header>
  <div class="doc-grid">
    <div><h3>Von</h3><p><strong>{{company_name}}</strong><br>{{company_address}}<br>{{company_city}}</p></div>
    <div><h3>An</h3><p><strong>{{customer_name}}</strong><br>{{customer_address}}</p></div>
  </div>
  {{#if subject}}<h2 class="doc-subject">{{subject}}</h2>{{/if}}
  {{#if intro_text}}<p>{{intro_text}}</p>{{/if}}
  <table class="doc-items">
    <thead><tr><th>Beschreibung</th><th>Anz.</th><th>Tage</th><th>Preis</th><th>Summe</th></tr></thead>
    <tbody>{{#each items}}<tr><td>{{description}}</td><td>{{units}}</td><td>{{days}}</td><td>{{unit_price}}</td><td>{{line_total}}</td></tr>{{/each}}</tbody>
  </table>
  <div class="doc-totals">
    <div>Netto: {{subtotal}}</div>
    <div>MwSt: {{tax_total}}</div>
    <div class="doc-grand">{{total}}</div>
  </div>
  {{#if closing_text}}<p>{{closing_text}}</p>{{/if}}
  <footer>{{footer_text}}<br>{{terms}}</footer>
</div>',
'.doc{font-family:var(--doc-font);color:#1a1a1a;padding:40px}
.doc-head{background:var(--primary,#f5c842);padding:24px;display:flex;justify-content:space-between;align-items:center;margin-bottom:30px;border-radius:8px}
.doc-logo{max-height:50px;background:#fff;padding:8px;border-radius:4px}
.doc-head h1{font-size:28px;margin:0;color:#1a1a1a}
.doc-head p{font-size:12px;margin:4px 0 0;color:#1a1a1a}
.doc-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:30px}
.doc-grid h3{font-size:11px;text-transform:uppercase;color:#999;margin-bottom:6px}
.doc-subject{font-size:18px;margin:20px 0 10px;color:#1a1a1a}
.doc-items{width:100%;border-collapse:collapse;margin:20px 0}
.doc-items th{padding:10px;text-align:left;font-size:11px;color:#999;text-transform:uppercase;border-bottom:1px solid #e5e5e5}
.doc-items td{padding:12px 10px;border-bottom:1px solid #f0f0f0}
.doc-totals{text-align:right;margin:20px 0;background:#f9f9f9;padding:20px;border-radius:8px}
.doc-totals div{margin:4px 0;font-size:13px}
.doc-grand{font-size:24px;font-weight:bold;color:var(--primary,#f5c842);border-top:1px solid #e5e5e5;padding-top:8px;margin-top:8px}
.doc footer{margin-top:30px;padding-top:20px;border-top:1px solid #e5e5e5;font-size:11px;color:#999;text-align:center}', false, true),

('minimal', 'Minimal', 'Schlichtes Schwarz-Weiß Layout',
'<div class="doc">
  <header class="doc-head"><h1>{{company_name}}</h1></header>
  <div class="doc-info"><span>{{doc_label}} <strong>{{doc_no}}</strong></span><span>{{issue_date}}</span></div>
  <div class="doc-customer"><strong>{{customer_name}}</strong><br>{{customer_address}}</div>
  {{#if subject}}<h2>{{subject}}</h2>{{/if}}
  {{#if intro_text}}<p>{{intro_text}}</p>{{/if}}
  <table class="doc-items">
    <thead><tr><th>Beschreibung</th><th>×</th><th>×</th><th>€</th><th>Summe</th></tr></thead>
    <tbody>{{#each items}}<tr><td>{{description}}</td><td>{{units}}</td><td>{{days}}</td><td>{{unit_price}}</td><td>{{line_total}}</td></tr>{{/each}}</tbody>
  </table>
  <div class="doc-totals">
    <div>{{subtotal}}</div><div>{{tax_total}}</div><div class="doc-grand">{{total}}</div>
  </div>
  {{#if closing_text}}<p>{{closing_text}}</p>{{/if}}
  <footer>{{terms}}</footer>
</div>',
'.doc{font-family:var(--doc-font);color:#000;padding:40px;line-height:1.6}
.doc-head h1{font-size:14px;text-transform:uppercase;letter-spacing:4px;margin-bottom:40px}
.doc-info{display:flex;justify-content:space-between;margin-bottom:30px;font-size:12px;border-bottom:1px solid #000;padding-bottom:10px}
.doc-customer{margin-bottom:30px;font-size:13px}
.doc h2{font-size:16px;margin:20px 0 10px;font-weight:normal}
.doc-items{width:100%;border-collapse:collapse;margin:30px 0;font-size:12px}
.doc-items th{padding:8px 4px;text-align:left;font-weight:normal;border-bottom:1px solid #000;font-size:10px;text-transform:uppercase}
.doc-items td{padding:8px 4px}
.doc-totals{text-align:right;margin:30px 0;font-size:13px}
.doc-grand{font-size:16px;font-weight:bold;border-top:1px solid #000;padding-top:8px;margin-top:8px}
.doc footer{margin-top:60px;font-size:10px;color:#666;text-align:center}', false, true),

('bold', 'Bold', 'Auffälliges Layout mit großen Headlines',
'<div class="doc">
  <header class="doc-head">
    {{#if logo}}<img src="{{logo}}" class="doc-logo">{{/if}}
    <h1>{{doc_label}}</h1>
    <p class="doc-no">{{doc_no}}</p>
  </header>
  <div class="doc-grid">
    <div><h3>Von</h3><strong>{{company_name}}</strong><br>{{company_address}}</div>
    <div><h3>An</h3><strong>{{customer_name}}</strong><br>{{customer_address}}</div>
    <div><h3>Datum</h3>{{issue_date}}<br><strong>Fällig:</strong> {{due_date}}</div>
  </div>
  {{#if subject}}<h2 class="doc-subject">{{subject}}</h2>{{/if}}
  <table class="doc-items">
    <thead><tr><th>Beschreibung</th><th>Anz.</th><th>Tage</th><th>Preis</th><th>Summe</th></tr></thead>
    <tbody>{{#each items}}<tr><td>{{description}}</td><td>{{units}}</td><td>{{days}}</td><td>{{unit_price}}</td><td>{{line_total}}</td></tr>{{/each}}</tbody>
  </table>
  <div class="doc-grand-section">
    <div class="doc-totals"><div>Netto: {{subtotal}}</div><div>MwSt: {{tax_total}}</div></div>
    <div class="doc-grand">{{total}}</div>
  </div>
  {{#if closing_text}}<p>{{closing_text}}</p>{{/if}}
  <footer>{{footer_text}}<br>{{terms}}</footer>
</div>',
'.doc{font-family:var(--doc-font);color:#1a1a1a;padding:40px}
.doc-head{text-align:center;padding:40px 0;margin-bottom:30px;border-bottom:8px solid var(--primary,#f5c842)}
.doc-logo{max-height:80px;margin-bottom:20px}
.doc-head h1{font-size:48px;text-transform:uppercase;letter-spacing:4px;margin:0;font-weight:900}
.doc-no{font-size:14px;color:#666;margin-top:8px}
.doc-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-bottom:30px}
.doc-grid h3{font-size:10px;text-transform:uppercase;color:#999;margin-bottom:6px;letter-spacing:2px}
.doc-subject{font-size:24px;margin:30px 0;text-transform:uppercase;letter-spacing:1px}
.doc-items{width:100%;border-collapse:collapse;margin:20px 0}
.doc-items th{background:#1a1a1a;color:#fff;padding:12px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:1px}
.doc-items td{padding:12px;border-bottom:1px solid #e5e5e5}
.doc-grand-section{display:flex;justify-content:space-between;align-items:center;background:var(--primary,#f5c842);padding:24px;margin:30px 0;border-radius:4px}
.doc-totals{font-size:13px}
.doc-totals div{margin:4px 0}
.doc-grand{font-size:36px;font-weight:900}
.doc footer{margin-top:30px;text-align:center;font-size:11px;color:#666}', false, true),

('yon-studio', 'Yon Studio', 'Professionelles 257 Studio Layout mit Logo oben rechts',
'<div class="doc">
  <header class="doc-head">
    {{#if logo}}<img src="{{logo}}" class="doc-logo" alt="Logo">{{/if}}
  </header>
  <section class="doc-meta">
    <div class="doc-customer">
      <strong>{{customer_name}}</strong><br>
      {{customer_address}}
    </div>
    <table class="doc-info">
      <tr><td>{{doc_label_no}}</td><td>{{doc_no}}</td></tr>
      <tr><td>{{doc_label_date}}</td><td>{{issue_date}}</td></tr>
      {{#if rental_period}}<tr><td>{{doc_label_delivery}}</td><td>{{rental_period}}</td></tr>{{/if}}
      {{#if customer_vat}}<tr><td>{{doc_label_vat}}</td><td>{{customer_vat}}</td></tr>{{/if}}
    </table>
  </section>
  {{#if subject}}<h1 class="doc-subject">{{subject}}</h1>{{/if}}
  {{#if intro_text}}<div class="doc-intro">{{intro_text}}</div>{{/if}}
  <table class="doc-items">
    <thead><tr>
      <th class="num">#</th>
      <th class="desc">Description</th>
      <th class="qty">Quantity</th>
      <th class="price">Unit price</th>
      <th class="total">Total price</th>
    </tr></thead>
    <tbody>{{#each items}}
      <tr>
        <td class="num">{{@index}}.</td>
        <td class="desc">{{description}}</td>
        <td class="qty">{{units_days}}</td>
        <td class="price">{{unit_price}}</td>
        <td class="total">{{line_total}}</td>
      </tr>
    {{/each}}</tbody>
  </table>
  <div class="doc-totals">
    <div class="row"><span>Sum positions</span><span class="amt">{{subtotal}}</span></div>
    {{#if production_fee_amount}}<div class="row"><span>Production Fee</span><span class="amt">{{production_fee_pct}} % ({{production_fee_amount}})</span></div>{{/if}}
    {{#if discount_amount}}<div class="row"><span>Discount</span><span class="amt">− {{discount_amount}}</span></div>{{/if}}
    <div class="row"><span>Total net</span><span class="amt">{{total_net}}</span></div>
    <div class="row"><span>Included VAT {{tax_rate_pct}}%</span><span class="amt">{{tax_total}}</span></div>
    <div class="row grand"><span>Total gross</span><span class="amt">{{total}}</span></div>
  </div>
  {{#if terms}}<p class="doc-terms">{{payment_label}}: {{terms}}</p>{{/if}}
  {{#if closing_text}}<p class="doc-closing">{{closing_text}}</p>{{/if}}
  {{#if signature_name}}<p class="doc-sig">{{signature_name}}</p>{{/if}}
  {{#if include_agb}}<div class="doc-agb">{{agb_text}}</div>{{/if}}
  <footer class="doc-footer">
    <div class="col">
      <strong>{{company_name}}</strong><br>
      {{company_address}}<br>
      {{company_email}}<br>
      {{company_website}}
    </div>
    <div class="col">
      {{#if company_court}}{{company_court}}<br>{{/if}}
      {{#if company_fn}}FN: {{company_fn}}<br>{{/if}}
      VAT/UID: {{company_vat}}
      {{#if company_tax_no}}<br>TAX-NO: {{company_tax_no}}{{/if}}
    </div>
    <div class="col right">
      <strong>BANK ACCOUNT:</strong><br>
      IBAN: {{company_iban}}<br>
      BIC: {{company_bic}}
    </div>
  </footer>
  <div class="doc-pageno">1/1</div>
</div>',
'@page { margin: 18mm 18mm 22mm 18mm; }
.doc { font-family: var(--doc-font); color: #1a1a1a; font-size: 10pt; line-height: 1.45; }
.doc-head { display: flex; justify-content: flex-end; align-items: flex-start; min-height: 80px; margin-bottom: 30px; }
.doc-logo { max-height: 70px; max-width: 200px; object-fit: contain; }
.doc-meta { display: grid; grid-template-columns: 1fr auto; gap: 40px; margin-bottom: 60px; align-items: start; }
.doc-customer { font-size: 11pt; line-height: 1.6; }
.doc-customer strong { display: block; margin-bottom: 2px; }
.doc-info { font-size: 9pt; border-collapse: collapse; }
.doc-info td { padding: 2px 0; vertical-align: top; }
.doc-info td:first-child { color: #666; padding-right: 20px; }
.doc-subject { font-size: 18pt; font-weight: 400; margin: 30px 0 25px 0; color: #1a1a1a; }
.doc-intro { margin-bottom: 18px; font-size: 10pt; }
.doc-items { width: 100%; border-collapse: collapse; margin: 20px 0 30px 0; }
.doc-items thead th { font-weight: 400; font-size: 9pt; color: #666; padding: 8px 0; border-bottom: 1px solid #d0d0d0; text-align: right; }
.doc-items thead th.num { width: 28px; text-align: left; }
.doc-items thead th.desc { text-align: left; }
.doc-items tbody td { padding: 10px 8px 10px 0; border-bottom: 1px solid #eee; vertical-align: top; font-size: 9.5pt; text-align: right; }
.doc-items tbody td.num { color: #888; text-align: left; padding-left: 0; }
.doc-items tbody td.desc { text-align: left; }
.doc-totals { width: 100%; margin: 0 0 30px 0; }
.doc-totals .row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 9.5pt; }
.doc-totals .row .amt { font-variant-numeric: tabular-nums; }
.doc-totals .row.grand { border-top: 1px solid #d0d0d0; padding-top: 10px; margin-top: 6px; font-weight: 700; font-size: 10pt; }
.doc-terms { margin-top: 30px; font-size: 9.5pt; }
.doc-closing { margin-top: 20px; font-size: 9.5pt; }
.doc-sig { margin-top: 8px; font-size: 9.5pt; }
.doc-agb { margin-top: 30px; padding-top: 20px; border-top: 1px solid #ccc; font-size: 8.5pt; color: #555; }
.doc-footer { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; margin-top: 60px; padding-top: 12px; border-top: 1px solid #d0d0d0; font-size: 8pt; color: #666; line-height: 1.5; }
.doc-footer strong { color: #1a1a1a; }
.doc-footer .right { text-align: right; }
.doc-pageno { text-align: right; font-size: 8pt; color: #999; margin-top: 10px; }', true, true);

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
