import React, { useEffect, useState, useRef } from "react";
import { api } from "../api";
import { useSettings } from "../context/SettingsContext";

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

export default function DocPreview({ doc, items, customer, layoutKey = "yon-studio" }) {
  const { settings, fmt } = useSettings();
  const [layouts, setLayouts] = useState([]);
  const ref = useRef(null);

  useEffect(() => { api.get("/settings/layouts").then(setLayouts).catch(console.error); }, []);

  const layout = layouts.find(l => l.key === layoutKey) || layouts[0];

  if (!layout) return <div style={{ padding:40, color:"#666" }}>Loading preview…</div>;

  const locale = settings.currency_locale || "de-AT";
  const lang = doc.doc_language || settings.language || "de";
  const isQuote = doc.doc_type === "quote";

  const L = lang === "en" ? {
    invoice: "Invoice", quote: "Quote",
    invoiceNo: "Invoice no.", quoteNo: "Quote no.",
    date: "Invoice date", quoteDate: "Quote date",
    delivery: "Period of performance", validUntil: "Valid until",
    vat: "Your VAT-ID", paymentLabel: "Terms of payment",
  } : {
    invoice: "Rechnung", quote: "Angebot",
    invoiceNo: "Rechnungsnr.", quoteNo: "Angebotsnr.",
    date: "Rechnungsdatum", quoteDate: "Angebotsdatum",
    delivery: "Leistungsdatum", validUntil: "Gültig bis",
    vat: "Ihre UID", paymentLabel: "Zahlungsbedingungen",
  };

  const fmtDate = (d, opts) => d ? new Date(d).toLocaleDateString(locale, opts) : "";

  const rentalStart = fmtDate(doc.rental_start);
  const rentalEnd = fmtDate(doc.rental_end);
  const rentalPeriod = rentalStart && rentalEnd ? `${rentalStart} – ${rentalEnd}` : rentalStart || rentalEnd;

  const subtotalRaw = Number(doc.subtotal || 0);
  const taxRaw = Number(doc.tax_total || 0);
  const prodFeeRaw = Number(doc.production_fee_amount || 0);
  const discountRaw = Number(doc.discount_amount || 0);
  const totalNet = subtotalRaw + prodFeeRaw - discountRaw;
  const totalGross = Number(doc.total || (totalNet + taxRaw));
  const taxRatePct = Math.round(Number(doc.items?.[0]?.tax_rate || settings.default_tax_rate || 0.20) * 100);

  const mappedItems = (items || []).map((it, i) => {
    const units = Number(it.units || 1);
    const days = Number(it.days || 1);
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
      description: it.description,
      units: String(units),
      days: String(days),
      units_days: unitsDaysLabel,
      unit_price: fmt(it.unit_price),
      line_total: fmt(units * days * Number(it.unit_price || 0)),
    };
  });

  const data = {
    doc_label: isQuote ? L.quote : L.invoice,
    doc_label_no: isQuote ? L.quoteNo : L.invoiceNo,
    doc_label_date: isQuote ? L.quoteDate : L.date,
    doc_label_delivery: L.delivery,
    doc_label_valid: L.validUntil,
    doc_label_vat: L.vat,
    payment_label: L.paymentLabel,
    doc_no: doc.doc_no || "—",
    subject: doc.subject || "",
    issue_date: fmtDate(doc.issue_date || new Date()),
    due_date: fmtDate(doc.due_date),
    valid_until: fmtDate(doc.valid_until),
    rental_start: rentalStart,
    rental_end: rentalEnd,
    rental_period: rentalPeriod,
    customer_name: customer?.name || "—",
    customer_address: (customer?.contacts || []).find(c => c.type === "address")?.value || "—",
    customer_vat: customer?.vat || "",
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
    signature_name: settings.signature_name || "",
    header_text: doc.header_text || "",
    intro_text: doc.intro_text || "",
    closing_text: doc.closing_text || "",
    footer_text: doc.footer_text || "",
    notes_text: doc.notes_text || "",
    terms: doc.terms || "",
    agb_text: doc.agb_text || "",
    include_agb: doc.include_agb,
    subtotal: fmt(subtotalRaw),
    tax_total: fmt(taxRaw),
    tax_rate_pct: taxRatePct,
    total_net: fmt(totalNet),
    total: fmt(totalGross),
    production_fee_pct: doc.production_fee_pct || "",
    production_fee_amount: prodFeeRaw > 0 ? fmt(prodFeeRaw) : "",
    discount_amount: discountRaw > 0 ? fmt(discountRaw) : "",
    items: mappedItems,
  };

  const html = render(layout.html, data);

  return (
    <div className="doc-preview" ref={ref}>
      <style>{layout.css.replace(/var\(--primary[^)]*\)/g, settings.primary_color || "#f5c842")}</style>
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

export function printPreview(html, css) {
  const win = window.open("", "_blank");
  if (!win) return alert("Bitte Pop-ups erlauben");
  win.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>Drucken</title><style>${css}</style></head><body>${html}</body></html>`);
  win.document.close();
  win.focus();
  setTimeout(() => win.print(), 250);
}
