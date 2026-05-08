import React, { useEffect, useState } from "react";
import { api } from "../api";
import { useI18n } from "../context/I18nContext";
import { useSettings } from "../context/SettingsContext";
import { useAuth } from "../context/AuthContext";

const Field = ({ label, children }) => (
  <div className="form-group"><label>{label}</label>{children}</div>
);

// Template picker — shows a dropdown with templates of the matching type
// Auto-fills the field when a template is chosen, or lets you write custom text.
const TplPicker = ({ label, field, type, form, setForm, templates }) => {
  const matching = templates.filter(t => t.type === type);
  const currentValue = form[field] || "";
  // Find which template matches (if any)
  const selectedTpl = matching.find(t => t.content === currentValue);
  return (
    <div className="form-group full">
      <label>{label}</label>
      <div style={{ display:"flex", gap:8, marginBottom:6 }}>
        <select
          value={selectedTpl?.id || ""}
          onChange={e => {
            const tpl = matching.find(t => t.id === e.target.value);
            setForm(p => ({...p, [field]: tpl ? tpl.content : ""}));
          }}
          style={{ flex:1 }}
        >
          <option value="">— Eigener Text / Vorlage wählen —</option>
          {matching.map(tpl => <option key={tpl.id} value={tpl.id}>{tpl.name}</option>)}
        </select>
      </div>
      <textarea value={currentValue} onChange={e => setForm(p => ({...p, [field]: e.target.value}))} />
      <small style={{ fontSize:11, color:"var(--text-dim)", marginTop:4 }}>
        Vorlagen kannst du im Bereich "Textvorlagen" verwalten.
      </small>
    </div>
  );
};

const FONT_OPTIONS = [
  "DM Sans", "Inter", "Roboto", "Open Sans", "Lato",
  "Montserrat", "Poppins", "Source Sans Pro", "Raleway",
  "Bebas Neue", "Playfair Display", "Merriweather", "Georgia",
  "Helvetica", "Arial", "Times New Roman", "Courier New", "DM Mono"
];

export default function Settings() {
  const { user } = useAuth();
  const { t, setLang } = useI18n();
  const { settings: ctx, reload } = useSettings();
  const [section, setSection] = useState("company");
  const [form, setForm] = useState({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const [categories, setCategories] = useState([]);
  const [newCat, setNewCat] = useState("");
  const [templates, setTemplates] = useState([]);
  const [newTpl, setNewTpl] = useState({ type:"intro", name:"", content:"" });
  const [layouts, setLayouts] = useState([]);
  const [layoutModal, setLayoutModal] = useState(null);
  const [layoutForm, setLayoutForm] = useState({ key:"", name:"", description:"", html:"", css:"" });

  useEffect(() => { setForm({...ctx}); }, [ctx]);
  useEffect(() => {
    api.get("/settings/categories").then(setCategories).catch(console.error);
    api.get("/settings/templates").then(setTemplates).catch(console.error);
    api.get("/settings/layouts").then(setLayouts).catch(console.error);
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

  async function saveLayout() {
    try {
      if (layoutModal === "add") await api.post("/settings/layouts", layoutForm);
      else await api.put(`/settings/layouts/${layoutForm.id}`, layoutForm);
      setLayoutModal(null);
      api.get("/settings/layouts").then(setLayouts);
    } catch(e) { alert(e.message); }
  }
  async function deleteLayout(id) {
    if (!window.confirm(t("delete")+"?")) return;
    try { await api.delete(`/settings/layouts/${id}`); api.get("/settings/layouts").then(setLayouts); }
    catch(e) { alert(e.message); }
  }

  if (user?.role !== "admin") return <div className="page"><p className="text-dim">Admin only.</p></div>;

  const SECTIONS = [
    { id:"company",   label:t("companyInfo") },
    { id:"appearance", label:t("appearance") },
    { id:"localization", label:t("currency")+" & "+t("language") },
    { id:"invoice",   label:t("invoiceSettings") },
    { id:"quote",     label:t("quoteSettings") },
    { id:"agb",       label:t("agb") },
    { id:"reminder",  label:t("reminderSettings") },
    { id:"categories", label:t("categoriesManagement") },
    { id:"templates", label:t("textTemplates") },
    { id:"layouts",   label:t("layout")+"s" },
  ];

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

      <div className="settings-layout">
        <nav className="settings-nav">
          {SECTIONS.map(s => (
            <button key={s.id} className={section===s.id?"active":""} onClick={()=>setSection(s.id)}>
              {s.label}
            </button>
          ))}
        </nav>

        <div>
          {section === "company" && (
            <div className="card">
              <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:20, fontSize:16 }}>{t("companyInfo")}</h3>
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
            </div>
          )}

          {section === "appearance" && (
            <div className="card">
              <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:20, fontSize:16 }}>{t("appearance")}</h3>
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
                <Field label="Programm-Schriftart">
                  <select value={form.app_font||"DM Sans"} onChange={f("app_font")}>
                    {FONT_OPTIONS.map(font => <option key={font} value={font}>{font}</option>)}
                  </select>
                </Field>
                <Field label="Dokument-Schriftart (Rechnungen/Angebote)">
                  <select value={form.doc_font||"DM Sans"} onChange={f("doc_font")}>
                    {FONT_OPTIONS.map(font => <option key={font} value={font}>{font}</option>)}
                  </select>
                </Field>
                <div className="form-group full">
                  <label>Custom Font CSS URL (z.B. Google Fonts)</label>
                  <input value={form.custom_font_url||""} onChange={f("custom_font_url")}
                    placeholder="https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap" />
                  <small className="text-dim" style={{ fontSize:11 }}>
                    Beispiel: https://fonts.googleapis.com/css2?family=Inter&display=swap
                  </small>
                </div>
              </div>
            </div>
          )}

          {section === "localization" && (
            <div className="card">
              <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:20, fontSize:16 }}>{t("currency")+" & "+t("language")}</h3>
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
            </div>
          )}

          {section === "invoice" && (
            <div className="card">
              <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:20, fontSize:16 }}>{t("invoiceSettings")}</h3>
              <div className="form-grid">
                <Field label={t("invoicePrefix")}><input value={form.invoice_prefix||""} onChange={f("invoice_prefix")} placeholder="RE" /></Field>
                <Field label={t("invoiceStart")}><input type="number" value={form.invoice_start||""} onChange={f("invoice_start")} placeholder="1000" /></Field>
                <TplPicker label={t("invoiceTerms")} field="invoice_terms" type="terms" form={form} setForm={setForm} templates={templates} />
                <TplPicker label={t("invoiceHeader")} field="invoice_header" type="header" form={form} setForm={setForm} templates={templates} />
                <TplPicker label={t("invoiceIntro")} field="invoice_intro" type="intro" form={form} setForm={setForm} templates={templates} />
                <TplPicker label={t("invoiceClosure")} field="invoice_closing" type="closing" form={form} setForm={setForm} templates={templates} />
                <TplPicker label={t("invoiceFooter")} field="invoice_footer" type="footer" form={form} setForm={setForm} templates={templates} />
              </div>
            </div>
          )}

          {section === "quote" && (
            <div className="card">
              <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:20, fontSize:16 }}>{t("quoteSettings")}</h3>
              <div className="form-grid">
                <Field label={t("quotePrefix")}><input value={form.quote_prefix||""} onChange={f("quote_prefix")} placeholder="AN" /></Field>
                <Field label={t("quoteStart")}><input type="number" value={form.quote_start||""} onChange={f("quote_start")} placeholder="100" /></Field>
                <TplPicker label={t("quoteTerms")} field="quote_terms" type="terms" form={form} setForm={setForm} templates={templates} />
                <TplPicker label={t("quoteHeader")} field="quote_header" type="header" form={form} setForm={setForm} templates={templates} />
                <TplPicker label={t("quoteIntro")} field="quote_intro" type="intro" form={form} setForm={setForm} templates={templates} />
                <TplPicker label={t("quoteClosure")} field="quote_closing" type="closing" form={form} setForm={setForm} templates={templates} />
                <TplPicker label={t("quoteFooter")} field="quote_footer" type="footer" form={form} setForm={setForm} templates={templates} />
              </div>
            </div>
          )}

          {section === "agb" && (
            <div className="card">
              <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:20, fontSize:16 }}>{t("agb")}</h3>
              <div className="form-group full">
                <label>{t("agbText")} (Standard)</label>
                <textarea value={form.agb_text||""} onChange={f("agb_text")} style={{ minHeight:200 }} />
              </div>
            </div>
          )}

          {section === "reminder" && (
            <div className="card">
              <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:20, fontSize:16 }}>{t("reminderSettings")}</h3>
              <div className="form-grid">
                <Field label={t("reminderDays")}><input type="number" value={form.reminder_days||""} onChange={f("reminder_days")} /></Field>
                <Field label={t("lateInterestRate")+" (z.B. 4)"}><input type="number" step="0.1" value={form.late_interest_rate||""} onChange={f("late_interest_rate")} /></Field>
                <Field label={t("reminder1Fee")+" (€)"}><input type="number" step="0.01" value={form.reminder1_fee||""} onChange={f("reminder1_fee")} /></Field>
                <Field label={t("reminder2Fee")+" (€)"}><input type="number" step="0.01" value={form.reminder2_fee||""} onChange={f("reminder2_fee")} /></Field>
                <Field label={t("reminder3Fee")+" (€)"}><input type="number" step="0.01" value={form.reminder3_fee||""} onChange={f("reminder3_fee")} /></Field>
              </div>
            </div>
          )}

          {section === "categories" && (
            <div className="card">
              <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:20, fontSize:16 }}>{t("categoriesManagement")}</h3>
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
            </div>
          )}

          {section === "templates" && (
            <div className="card">
              <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:20, fontSize:16 }}>{t("textTemplates")}</h3>
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
            </div>
          )}

          {section === "layouts" && (
            <div className="card">
              <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:20 }}>
                <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, fontSize:16 }}>{t("layout")}s</h3>
                <button className="btn btn-primary btn-sm" onClick={() => { setLayoutForm({ key:"", name:"", description:"", html:"", css:"" }); setLayoutModal("add"); }}>+ Layout</button>
              </div>
              <p className="text-dim" style={{ fontSize:12, marginBottom:16 }}>
                Layouts verwenden Mustache-Syntax: <code>{`{{customer_name}}`}</code>, <code>{`{{#each items}}…{{/each}}`}</code>, <code>{`{{#if subject}}…{{/if}}`}</code>
              </p>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Key</th><th>Name</th><th>{t("description")}</th><th>Typ</th><th></th></tr></thead>
                  <tbody>
                    {layouts.map(l => (
                      <tr key={l.id}>
                        <td className="mono text-dim">{l.key}</td>
                        <td><strong>{l.name}</strong></td>
                        <td className="text-dim" style={{ fontSize:12 }}>{l.description}</td>
                        <td>{l.is_builtin ? <span className="badge badge-paid">Built-in</span> : <span className="badge badge-draft">Custom</span>}</td>
                        <td style={{ textAlign:"right" }}>
                          <button className="btn btn-ghost btn-sm" onClick={() => { setLayoutForm({...l}); setLayoutModal("edit"); }}>{t("edit")}</button>
                          {!l.is_builtin && <button className="btn btn-danger btn-sm" style={{ marginLeft:6 }} onClick={() => deleteLayout(l.id)}>{t("delete")}</button>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Layout edit modal */}
      {layoutModal && (
        <div className="modal-overlay" onClick={() => setLayoutModal(null)}>
          <div className="modal modal-lg" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{layoutModal==="add" ? "+ LAYOUT" : "EDIT LAYOUT"}</h3>
              <button className="btn btn-ghost btn-icon" onClick={() => setLayoutModal(null)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="form-grid">
                <Field label="Key (URL-safe)">
                  <input value={layoutForm.key} onChange={e=>setLayoutForm(p=>({...p,key:e.target.value}))} placeholder="my-layout" disabled={layoutModal==="edit"} />
                </Field>
                <Field label="Name"><input value={layoutForm.name} onChange={e=>setLayoutForm(p=>({...p,name:e.target.value}))} /></Field>
                <div className="form-group full"><label>{t("description")}</label>
                  <input value={layoutForm.description||""} onChange={e=>setLayoutForm(p=>({...p,description:e.target.value}))} />
                </div>
                <div className="form-group full"><label>HTML Template</label>
                  <textarea value={layoutForm.html} onChange={e=>setLayoutForm(p=>({...p,html:e.target.value}))} style={{ minHeight:200, fontFamily:"var(--mono)", fontSize:12 }} />
                </div>
                <div className="form-group full"><label>CSS</label>
                  <textarea value={layoutForm.css} onChange={e=>setLayoutForm(p=>({...p,css:e.target.value}))} style={{ minHeight:200, fontFamily:"var(--mono)", fontSize:12 }} />
                </div>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setLayoutModal(null)}>{t("cancel")}</button>
              <button className="btn btn-primary" onClick={saveLayout}>{t("save")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
