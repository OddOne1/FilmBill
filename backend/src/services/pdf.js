const puppeteer = require("puppeteer");
const db = require("../db");

// Same mustache-style template engine as the frontend DocPreview
function render(tpl, data) {
  if (!tpl) return "";
  let out = tpl;
  out = out.replace(/\{\{#each\s+(\w+)\}\}([\s\S]*?)\{\{\/each\}\}/g, (_, key, body) => {
    const list = data[key];
    if (!Array.isArray(list)) return "";
    return list.map((item, i) => {
      let chunk = body;
      chunk = chunk.replace(/\{\{@index\}\}/g, String(i + 1));
      chunk = chunk.replace(/\{\{(\w+)\}\}/g, (__, k) => item[k] != null ? String(item[k]) : "");
      return chunk;
    }).join("");
  });
  out = out.replace(/\{\{#if\s+(\w+)\}\}([\s\S]*?)\{\{\/if\}\}/g, (_, key, body) => {
    const v = data[key];
    if (v && (Array.isArray(v) ? v.length > 0 : true)) return body;
    return "";
  });
  out = out.replace(/\{\{(\w+)\}\}/g, (_, k) => data[k] != null ? String(data[k]) : "");
  return out;
}

function fmtCurrency(v, locale = "de-AT", code = "EUR") {
  try {
    return Number(v||0).toLocaleString(locale, { style:"currency", currency:code });
  } catch {
    return "€ " + Number(v||0).toFixed(2);
  }
}

async function getSettings() {
  const { rows } = await db.query("SELECT key, value FROM settings");
  const obj = {}; rows.forEach(r => { obj[r.key] = r.value; });
  return obj;
}

async function getLayout(layoutKey) {
  const { rows } = await db.query("SELECT * FROM doc_layouts WHERE key=$1 LIMIT 1", [layoutKey]);
  if (rows[0]) return rows[0];
  // fallback to first layout
  const all = await db.query("SELECT * FROM doc_layouts ORDER BY is_builtin DESC LIMIT 1");
  return all.rows[0];
}

async function buildDocHtml(invoice) {
  const settings = await getSettings();
  const locale = settings.currency_locale || "de-AT";
  const code = settings.currency_code || "EUR";
  const layout = await getLayout(invoice.doc_layout || "yon-studio");
  const lang = invoice.doc_language || settings.language || "de";

  // Localized labels
  const L = lang === "en" ? {
    invoice: "Invoice", quote: "Quote",
    invoiceNo: "Invoice no.", quoteNo: "Quote no.",
    date: "Invoice date", quoteDate: "Quote date",
    delivery: "Delivery date", validUntil: "Valid until",
    vat: "Your VAT-ID",
  } : {
    invoice: "Rechnung", quote: "Angebot",
    invoiceNo: "Rechnungsnr.", quoteNo: "Angebotsnr.",
    date: "Rechnungsdatum", quoteDate: "Angebotsdatum",
    delivery: "Leistungsdatum", validUntil: "Gültig bis",
    vat: "Ihre UID",
  };

  const isQuote = invoice.doc_type === "quote";

  const items = (invoice.items || []).map(it => {
    const units = Number(it.units || 1);
    const days = Number(it.days || 1);
    const lineTotal = units * days * Number(it.unit_price || 0);
    let unitsDaysLabel;
    if (days > 1 || (days === 1 && units > 1)) {
      const qty = days > 1 ? days : units;
      unitsDaysLabel = `${qty.toFixed(2)} ${lang === "en" ? "day(s)" : "Tag(e)"}`;
    } else if (units === 1 && days === 1) {
      unitsDaysLabel = lang === "en" ? "blanket" : "Pausch.";
    } else {
      unitsDaysLabel = `${units.toFixed(2)}`;
    }
    return {
      description: it.description || "",
      units, days,
      units_days: unitsDaysLabel,
      unit_price: fmtCurrency(it.unit_price, locale, code),
      line_total: fmtCurrency(lineTotal, locale, code),
    };
  });

  // Compute totals if not provided
  const subtotalRaw = invoice.subtotal != null ? Number(invoice.subtotal)
    : items.reduce((s, it) => s + it.units * it.days * Number(invoice.items.find(x=>x.description===it.description)?.unit_price || 0), 0);
  const taxRaw = Number(invoice.tax_total || 0);
  const prodFeeRaw = Number(invoice.production_fee_amount || 0);
  const discountRaw = Number(invoice.discount_amount || 0);
  const totalNet = subtotalRaw + prodFeeRaw - discountRaw;
  const totalGross = Number(invoice.total || (totalNet + taxRaw));

  // Tax rate percentage (use first item's rate as representative)
  const firstTaxRate = (invoice.items && invoice.items[0]?.tax_rate) || 0.20;
  const taxRatePct = Math.round(Number(firstTaxRate) * 100);

  const paymentLabel = lang === "en" ? "Terms of payment" : "Zahlungsbedingungen";

  const data = {
    doc_label: isQuote ? L.quote : L.invoice,
    payment_label: paymentLabel,
    doc_label_no: isQuote ? L.quoteNo : L.invoiceNo,
    doc_label_date: isQuote ? L.quoteDate : L.date,
    doc_label_delivery: L.delivery,
    doc_label_valid: L.validUntil,
    doc_label_vat: L.vat,
    doc_no: invoice.doc_no || "—",
    subject: invoice.subject || "",
    issue_date: invoice.issue_date ? new Date(invoice.issue_date).toLocaleDateString(locale, { day:'numeric', month:'long', year:'numeric' }) : "",
    due_date: invoice.due_date ? new Date(invoice.due_date).toLocaleDateString(locale) : "",
    valid_until: invoice.valid_until ? new Date(invoice.valid_until).toLocaleDateString(locale) : "",
    rental_start: invoice.rental_start ? new Date(invoice.rental_start).toLocaleDateString(locale, { day:'numeric', month:'long', year:'numeric' }) : "",
    rental_end: invoice.rental_end ? new Date(invoice.rental_end).toLocaleDateString(locale, { day:'numeric', month:'long', year:'numeric' }) : "",
    rental_period: (() => {
      const s = invoice.rental_start ? new Date(invoice.rental_start).toLocaleDateString(locale, { day:'numeric', month:'long', year:'numeric' }) : "";
      const e = invoice.rental_end ? new Date(invoice.rental_end).toLocaleDateString(locale, { day:'numeric', month:'long', year:'numeric' }) : "";
      if (s && e) return `${s} – ${e}`;
      return s || e;
    })(),
    customer_name: invoice.customer_name || "—",
    customer_address: invoice.customer_address || "",
    customer_vat: invoice.customer_vat || "",
    company_name: settings.company_name || "",
    company_address: settings.company_address || "",
    company_city: settings.company_city || "",
    company_country: settings.company_country || "",
    company_phone: settings.company_phone || "",
    company_email: settings.company_email || "",
    company_website: settings.company_website || "",
    company_vat: settings.company_vat || "",
    company_iban: settings.company_iban || "",
    company_bic: settings.company_bic || "",
    company_court: settings.company_court || "",
    company_fn: settings.company_fn || "",
    company_tax_no: settings.company_tax_no || "",
    logo: settings.company_logo || "",
    header_text: invoice.header_text || "",
    intro_text: invoice.intro_text || "",
    closing_text: invoice.closing_text || "",
    footer_text: invoice.footer_text || "",
    notes_text: invoice.notes_text || "",
    terms: invoice.terms || "",
    agb_text: invoice.agb_text || "",
    include_agb: invoice.include_agb,
    signature_name: settings.signature_name || "",
    subtotal: fmtCurrency(subtotalRaw, locale, code),
    tax_total: fmtCurrency(taxRaw, locale, code),
    tax_rate_pct: taxRatePct,
    total_net: fmtCurrency(totalNet, locale, code),
    total: fmtCurrency(totalGross, locale, code),
    production_fee_pct: invoice.production_fee_pct || "",
    production_fee_amount: prodFeeRaw > 0 ? fmtCurrency(prodFeeRaw, locale, code) : "",
    discount_amount: discountRaw > 0 ? fmtCurrency(discountRaw, locale, code) : "",
    items,
  };

  const bodyHtml = render(layout.html, data);
  const css = layout.css.replace(/var\(--primary[^)]*\)/g, settings.primary_color || "#f5c842");
  const docFont = settings.doc_font || "DM Sans";
  const customFontUrl = settings.custom_font_url || "";

  return `<!DOCTYPE html>
<html lang="${invoice.doc_language || 'de'}">
<head>
<meta charset="utf-8">
<title>${data.doc_label} ${data.doc_no}</title>
${customFontUrl ? `<link rel="stylesheet" href="${customFontUrl}">` : ""}
<style>
  :root { --doc-font: "${docFont}", sans-serif; --primary: ${settings.primary_color || "#f5c842"}; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: var(--doc-font); background: #fff; color: #1a1a1a; }
  ${css}
</style>
</head>
<body>${bodyHtml}</body>
</html>`;
}

let _browser = null;
async function getBrowser() {
  if (_browser && _browser.connected) return _browser;
  _browser = await puppeteer.launch({
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
  });
  return _browser;
}

async function generatePdf(invoice) {
  const html = await buildDocHtml(invoice);
  const browser = await getBrowser();
  const page = await browser.newPage();
  try {
    await page.setContent(html, { waitUntil: "networkidle0", timeout: 30000 });
    const pdf = await page.pdf({
      format: "A4",
      printBackground: true,
      margin: { top: "10mm", right: "10mm", bottom: "10mm", left: "10mm" },
    });
    return pdf;
  } finally {
    await page.close();
  }
}

module.exports = { generatePdf, buildDocHtml };
