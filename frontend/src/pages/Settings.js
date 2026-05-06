import React, { useEffect, useState } from "react";
import { api } from "../api";
import { useI18n } from "../context/I18nContext";
import { useSettings } from "../context/SettingsContext";
import { useAuth } from "../context/AuthContext";

const SECTION = ({ title, children }) => (
  <div className="card" style={{ marginBottom:20 }}>
    <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:20, fontSize:16, borderBottom:"1px solid var(--border)", paddingBottom:12 }}>
      {title}
    </h3>
    {children}
  </div>
);

const Field = ({ label, children }) => (
  <div className="form-group"><label>{label}</label>{children}</div>
);

export default function Settings() {
  const { user } = useAuth();
  const { t, setLang } = useI18n();
  const { settings: ctx, reload } = useSettings();
  const [form, setForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // Categories
  const [categories, setCategories] = useState([]);
  const [newCat, setNewCat] = useState("");

  // Templates
  const [templates, setTemplates] = useState([]);
  const [newTpl, setNewTpl] = useState({ type:"intro", name:"", content:"" });

  useEffect(() => { setForm({...ctx}); }, [ctx]);
  useEffect(() => {
    api.get("/settings/categories").then(setCategories).catch(console.error);
    api.get("/settings/templates").then(setTemplates).catch(console.error);
  }, []);

  const f = k => e => setForm(p => ({...p, [k]: e.target.value}));

  async function save() {
    setSaving(true); setSaved(false);
    try {
      await api.put("/settings", form);
      if (form.language) setLang(form.language);
      reload(); setSaved(true);
      setTimeout(()=>setSaved(false), 3000);
    } catch(e) { alert(e.message); }
    finally { setSaving(false); }
  }

  async function addCategory() {
    if (!newCat.trim()) return;
    await api.post("/settings/categories", { name: newCat });
    setNewCat("");
    api.get("/settings/categories").then(setCategories);
  }
  async function deleteCategory(id) {
    if (!window.confirm(t("delete")+"?")) return;
    await api.delete(`/settings/categories/${id}`);
    api.get("/settings/categories").then(setCategories);
  }

  async function addTemplate() {
    if (!newTpl.name || !newTpl.content) return;
    await api.post("/settings/templates", newTpl);
    setNewTpl({ type:"intro", name:"", content:"" });
    api.get("/settings/templates").then(setTemplates);
  }
  async function deleteTemplate(id) {
    if (!window.confirm(t("delete")+"?")) return;
    await api.delete(`/settings/templates/${id}`);
    api.get("/settings/templates").then(setTemplates);
  }

  if (user?.role !== "admin") return <div className="page"><p className="text-dim">Admin only.</p></div>;

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("settings").toUpperCase()}</h2>
        <div className="flex gap-2 ml-auto">
          {saved && <span className="text-green" style={{ fontSize:13 }}>✓ {t("settingsSaved")}</span>}
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? t("saving") : t("save")}
          </button>
        </div>
      </div>

      <SECTION title={t("companyInfo")}>
        <div className="form-grid">
          <Field label={t("companyName")}><input value={form.company_name||""} onChange={f("company_name")} /></Field>
          <Field label={t("companyEmail")}><input value={form.company_email||""} onChange={f("company_email")} /></Field>
          <Field label={t("companyPhone")}><input value={form.company_phone||""} onChange={f("company_phone")} /></Field>
          <Field label={t("companyWebsite")}><input value={form.company_website||""} onChange={f("company_website")} /></Field>
          <Field label={t("companyAddress")}><input value={form.company_address||""} onChange={f("company_address")} /></Field>
          <Field label={t("companyCity")}><input value={form.company_city||""} onChange={f("company_city")} /></Field>
          <Field label={t("companyCountry")}><input value={form.company_country||""} onChange={f("company_country")} /></Field>
          <Field label={t("companyVat")}><input value={form.company_vat||""} onChange={f("company_vat")} /></Field>
          <Field label={t("companyBank")}><input value={form.company_bank||""} onChange={f("company_bank")} /></Field>
          <Field label={t("companyIban")}><input value={form.company_iban||""} onChange={f("company_iban")} /></Field>
          <Field label={t("companyBic")}><input value={form.company_bic||""} onChange={f("company_bic")} /></Field>
          <div className="form-group full">
            <label>{t("companyLogo")}</label>
            <input value={form.company_logo||""} onChange={f("company_logo")} placeholder="https://... oder data:image/png;base64,..." />
            {form.company_logo && <img src={form.company_logo} alt="Logo" style={{ marginTop:8, maxHeight:60, objectFit:"contain" }} />}
          </div>
        </div>
      </SECTION>

      <SECTION title={t("appearance")}>
        <div className="form-grid">
          <Field label={t("primaryColor")}>
            <input type="color" value={form.primary_color||"#f5c842"} onChange={f("primary_color")} style={{ height:40 }} />
          </Field>
          <Field label={t("secondaryColor")}>
            <input type="color" value={form.secondary_color||"#4dabf7"} onChange={f("secondary_color")} style={{ height:40 }} />
          </Field>
          <Field label={t("bgColor")}>
            <input type="color" value={form.bg_color||"#0d0d0d"} onChange={f("bg_color")} style={{ height:40 }} />
          </Field>
          <Field label={t("surfaceColor")}>
            <input type="color" value={form.surface_color||"#161616"} onChange={f("surface_color")} style={{ height:40 }} />
          </Field>
        </div>
      </SECTION>

      <SECTION title={t("currency")+" & "+t("language")}>
        <div className="form-grid">
          <Field label={t("language")}>
            <select value={form.language||"de"} onChange={f("language")}>
              <option value="de">Deutsch</option>
              <option value="en">English</option>
            </select>
          </Field>
          <Field label={t("currencySymbol")}><input value={form.currency_symbol||""} onChange={f("currency_symbol")} placeholder="€" /></Field>
          <Field label={t("currencyCode")}><input value={form.currency_code||""} onChange={f("currency_code")} placeholder="EUR" /></Field>
          <Field label={t("currencyLocale")}><input value={form.currency_locale||""} onChange={f("currency_locale")} placeholder="de-AT" /></Field>
          <Field label={t("defaultTaxRate")+" (z.B. 0.20)"}>
            <input type="number" step="0.01" value={form.default_tax_rate||""} onChange={f("default_tax_rate")} />
          </Field>
          <Field label={t("taxMode")}>
            <select value={form.tax_mode||"per_item"} onChange={f("tax_mode")}>
              <option value="per_item">{t("perItem")}</option>
              <option value="manual">{t("manualTax")}</option>
            </select>
          </Field>
        </div>
      </SECTION>

      <SECTION title={t("invoiceSettings")}>
        <div className="form-grid">
          <Field label={t("invoicePrefix")}><input value={form.invoice_prefix||""} onChange={f("invoice_prefix")} placeholder="RE" /></Field>
          <Field label={t("invoiceStart")}><input type="number" value={form.invoice_start||""} onChange={f("invoice_start")} placeholder="1000" /></Field>
          <div className="form-group full"><label>{t("invoiceTerms")}</label><textarea value={form.invoice_terms||""} onChange={f("invoice_terms")} /></div>
          <div className="form-group full"><label>{t("invoiceHeader")}</label><textarea value={form.invoice_header||""} onChange={f("invoice_header")} /></div>
          <div className="form-group full"><label>{t("invoiceIntro")}</label><textarea value={form.invoice_intro||""} onChange={f("invoice_intro")} /></div>
          <div className="form-group full"><label>{t("invoiceClosure")}</label><textarea value={form.invoice_closing||""} onChange={f("invoice_closing")} /></div>
          <div className="form-group full"><label>{t("invoiceFooter")}</label><textarea value={form.invoice_footer||""} onChange={f("invoice_footer")} /></div>
        </div>
      </SECTION>

      <SECTION title={t("quoteSettings")}>
        <div className="form-grid">
          <Field label={t("quotePrefix")}><input value={form.quote_prefix||""} onChange={f("quote_prefix")} placeholder="AN" /></Field>
          <Field label={t("quoteStart")}><input type="number" value={form.quote_start||""} onChange={f("quote_start")} placeholder="100" /></Field>
          <div className="form-group full"><label>{t("quoteTerms")}</label><textarea value={form.quote_terms||""} onChange={f("quote_terms")} /></div>
          <div className="form-group full"><label>{t("quoteHeader")}</label><textarea value={form.quote_header||""} onChange={f("quote_header")} /></div>
          <div className="form-group full"><label>{t("quoteIntro")}</label><textarea value={form.quote_intro||""} onChange={f("quote_intro")} /></div>
          <div className="form-group full"><label>{t("quoteClosure")}</label><textarea value={form.quote_closing||""} onChange={f("quote_closing")} /></div>
          <div className="form-group full"><label>{t("quoteFooter")}</label><textarea value={form.quote_footer||""} onChange={f("quote_footer")} /></div>
        </div>
      </SECTION>

      <SECTION title={t("agb")}>
        <div className="form-group full">
          <label>{t("agbText")} (Standard)</label>
          <textarea value={form.agb_text||""} onChange={f("agb_text")} style={{ minHeight:120 }} />
        </div>
      </SECTION>

      <SECTION title={t("reminderSettings")}>
        <div className="form-grid">
          <Field label={t("reminderDays")}><input type="number" value={form.reminder_days||""} onChange={f("reminder_days")} /></Field>
          <Field label={t("lateInterestRate")+" (z.B. 4)"}><input type="number" step="0.1" value={form.late_interest_rate||""} onChange={f("late_interest_rate")} /></Field>
          <Field label={t("reminder1Fee")+" (€)"}><input type="number" step="0.01" value={form.reminder1_fee||""} onChange={f("reminder1_fee")} /></Field>
          <Field label={t("reminder2Fee")+" (€)"}><input type="number" step="0.01" value={form.reminder2_fee||""} onChange={f("reminder2_fee")} /></Field>
          <Field label={t("reminder3Fee")+" (€)"}><input type="number" step="0.01" value={form.reminder3_fee||""} onChange={f("reminder3_fee")} /></Field>
        </div>
      </SECTION>

      <SECTION title={t("categoriesManagement")}>
        <div style={{ display:"flex", gap:8, marginBottom:16 }}>
          <input value={newCat} onChange={e=>setNewCat(e.target.value)} placeholder={t("addCategory")} style={{ maxWidth:300 }} onKeyDown={e=>e.key==="Enter"&&addCategory()} />
          <button className="btn btn-primary btn-sm" onClick={addCategory}>{t("add")}</button>
        </div>
        <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill, minmax(200px, 1fr))", gap:8 }}>
          {categories.map(c => (
            <div key={c.id} style={{ display:"flex", justifyContent:"space-between", padding:"8px 12px", background:"var(--surface2)", borderRadius:4 }}>
              <span style={{ fontSize:13 }}>{c.name}</span>
              <button className="btn btn-ghost btn-sm" onClick={()=>deleteCategory(c.id)} style={{ fontSize:11, padding:"0 6px" }}>✕</button>
            </div>
          ))}
        </div>
      </SECTION>

      <SECTION title={t("textTemplates")}>
        <div style={{ marginBottom:16, display:"grid", gridTemplateColumns:"140px 1fr 2fr auto", gap:8 }}>
          <select value={newTpl.type} onChange={e=>setNewTpl(p=>({...p,type:e.target.value}))}>
            <option value="header">{t("headerText")}</option>
            <option value="intro">{t("introText")}</option>
            <option value="closing">{t("closingText")}</option>
            <option value="footer">{t("footerText")}</option>
            <option value="notes">{t("notes")}</option>
            <option value="agb">{t("agb")}</option>
          </select>
          <input value={newTpl.name} onChange={e=>setNewTpl(p=>({...p,name:e.target.value}))} placeholder={t("templateName")} />
          <input value={newTpl.content} onChange={e=>setNewTpl(p=>({...p,content:e.target.value}))} placeholder={t("templateContent")} />
          <button className="btn btn-primary btn-sm" onClick={addTemplate}>{t("add")}</button>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>{t("templateType")}</th><th>{t("templateName")}</th><th>{t("templateContent")}</th><th></th></tr></thead>
            <tbody>
              {templates.map(tp => (
                <tr key={tp.id}>
                  <td><span className="badge badge-draft">{tp.type}</span></td>
                  <td><strong>{tp.name}</strong></td>
                  <td className="text-dim" style={{ fontSize:12 }}>{tp.content.slice(0, 80)}{tp.content.length>80?"…":""}</td>
                  <td style={{ textAlign:"right" }}>
                    <button className="btn btn-danger btn-sm" onClick={()=>deleteTemplate(tp.id)}>{t("delete")}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SECTION>
    </div>
  );
}
