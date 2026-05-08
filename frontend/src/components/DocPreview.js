import React, { useEffect, useState, useRef } from "react";
import { api } from "../api";
import { useSettings } from "../context/SettingsContext";

// Lightweight mustache-style template engine
// Supports {{var}}, {{#if var}}…{{/if}}, {{#each list}}…{{/each}}, {{@index}}
function render(tpl, data) {
  if (!tpl) return "";
  let out = tpl;
  // {{#each list}}…{{/each}}
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
  // {{#if var}}…{{/if}}
  out = out.replace(/\{\{#if\s+(\w+)\}\}([\s\S]*?)\{\{\/if\}\}/g, (_, key, body) => {
    const v = data[key];
    if (v && (Array.isArray(v) ? v.length > 0 : true)) return body;
    return "";
  });
  // {{var}}
  out = out.replace(/\{\{(\w+)\}\}/g, (_, k) => data[k] != null ? String(data[k]) : "");
  return out;
}

export default function DocPreview({ doc, items, customer, layoutKey = "classic" }) {
  const { settings, fmt } = useSettings();
  const [layouts, setLayouts] = useState([]);
  const ref = useRef(null);

  useEffect(() => { api.get("/settings/layouts").then(setLayouts).catch(console.error); }, []);

  const layout = layouts.find(l => l.key === layoutKey) || layouts[0];

  if (!layout) return <div style={{ padding:40, color:"#666" }}>Loading preview…</div>;

  const data = {
    doc_label: doc.doc_type === "quote" ? "Angebot" : "Rechnung",
    doc_no: doc.doc_no || "—",
    subject: doc.subject || "",
    issue_date: doc.issue_date ? new Date(doc.issue_date).toLocaleDateString(settings.currency_locale||"de-AT") : "",
    due_date: doc.due_date ? new Date(doc.due_date).toLocaleDateString(settings.currency_locale||"de-AT") : "",
    valid_until: doc.valid_until ? new Date(doc.valid_until).toLocaleDateString(settings.currency_locale||"de-AT") : "",
    rental_start: doc.rental_start ? new Date(doc.rental_start).toLocaleDateString(settings.currency_locale||"de-AT") : "",
    rental_end: doc.rental_end ? new Date(doc.rental_end).toLocaleDateString(settings.currency_locale||"de-AT") : "",
    customer_name: customer?.name || "—",
    customer_address: [customer?.address, customer?.city, customer?.country].filter(Boolean).join(", ") || "—",
    company_name: settings.company_name || "",
    company_address: settings.company_address || "",
    company_city: settings.company_city || "",
    company_country: settings.company_country || "",
    company_phone: settings.company_phone || "",
    company_email: settings.company_email || "",
    company_vat: settings.company_vat || "",
    company_iban: settings.company_iban || "",
    logo: settings.company_logo || "",
    header_text: doc.header_text || "",
    intro_text: doc.intro_text || "",
    closing_text: doc.closing_text || "",
    footer_text: doc.footer_text || "",
    notes_text: doc.notes_text || "",
    terms: doc.terms || "",
    agb_text: doc.agb_text || "",
    include_agb: doc.include_agb,
    subtotal: fmt(doc.subtotal||0),
    tax_total: fmt(doc.tax_total||0),
    total: fmt(doc.total||0),
    discount: fmt(doc.discount_amount||0),
    items: (items || []).map(it => ({
      description: it.description,
      units: it.units,
      days: it.days,
      unit_price: fmt(it.unit_price),
      line_total: fmt(Number(it.units||1)*Number(it.days||1)*Number(it.unit_price||0)),
    })),
  };

  const html = render(layout.html, data);

  return (
    <div className="doc-preview" ref={ref}>
      <style>{layout.css.replace(/var\(--primary[^)]*\)/g, settings.primary_color || "#f5c842")}</style>
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

// Print-only function for printing the preview
export function printPreview(html, css) {
  const win = window.open("", "_blank");
  if (!win) return alert("Bitte Pop-ups erlauben");
  win.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>Drucken</title><style>${css}</style></head><body>${html}</body></html>`);
  win.document.close();
  win.focus();
  setTimeout(() => win.print(), 250);
}
