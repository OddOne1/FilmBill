import React, { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useI18n } from "../context/I18nContext";
import { useSettings } from "../context/SettingsContext";

function addDays(n) { const d=new Date(); d.setDate(d.getDate()+n); return d.toISOString().slice(0,10); }
const today = () => new Date().toISOString().slice(0,10);
function newLine(defaultTax) { return { key:Date.now()+Math.random(), inventory_id:"", description:"", units:"1", days:"1", unit_price:"0", tax_rate:String(defaultTax||0.20) }; }

const LAYOUTS = [
  { id:"classic", name:"Klassisch" },
  { id:"modern",  name:"Modern" },
  { id:"minimal", name:"Minimal" },
  { id:"bold",    name:"Bold" },
];

export default function InvoiceEditor({ docType="invoice" }) {
  const { id } = useParams();
  const isNew = !id || id === "new";
  const navigate = useNavigate();
  const { t } = useI18n();
  const { settings, fmt } = useSettings();

  const [customers, setCustomers] = useState([]);
  const [inventory, setInventory] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [doc, setDoc] = useState(null);
  const [payments, setPayments] = useState([]);

  const [form, setForm] = useState({
    customer_id:"", subject:"",
    due_date: addDays(30), valid_until: addDays(30),
    rental_start:"", rental_end:"",
    header_text:"", intro_text:"", closing_text:"", footer_text:"", notes_text:"",
    agb_text:"", include_agb:false,
    terms:"",
    discount_type:"none", discount_value:"0",
    production_fee_pct:"0", manual_tax:"",
    doc_language: settings.language||"de",
    doc_layout: settings.invoice_layout||"classic",
    extra_recipients:"",
  });
  const [lines, setLines] = useState([newLine(settings.default_tax_rate)]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [payModal, setPayModal] = useState(false);
  const [reminderModal, setReminderModal] = useState(false);
  const [sendModal, setSendModal] = useState(false);
  const [extraEmail, setExtraEmail] = useState("");
  const [payForm, setPayForm] = useState({ amount:"", method:"Überweisung", reference:"", notes:"", paid_at: today() });

  useEffect(() => {
    api.get("/customers").then(setCustomers).catch(console.error);
    api.get("/inventory?active=true").then(setInventory).catch(console.error);
    api.get("/settings/templates").then(setTemplates).catch(console.error);
  }, []);

  useEffect(() => {
    if (!isNew) {
      api.get(`/invoices/${id}`).then(inv => {
        setDoc(inv);
        setForm({
          customer_id:inv.customer_id, subject:inv.subject||"",
          due_date:inv.due_date?.slice(0,10)||"",
          valid_until:inv.valid_until?.slice(0,10)||"",
          rental_start:inv.rental_start?.slice(0,10)||"",
          rental_end:inv.rental_end?.slice(0,10)||"",
          header_text:inv.header_text||"", intro_text:inv.intro_text||"",
          closing_text:inv.closing_text||"", footer_text:inv.footer_text||"",
          notes_text:inv.notes_text||"",
          agb_text:inv.agb_text||"", include_agb:inv.include_agb||false,
          terms:inv.terms||"",
          discount_type:inv.discount_type||"none",
          discount_value:String(inv.discount_value||0),
          production_fee_pct:String(inv.production_fee_pct||0),
          manual_tax:inv.manual_tax!=null?String(inv.manual_tax):"",
          doc_language:inv.doc_language||"de",
          doc_layout:inv.doc_layout||"classic",
          extra_recipients:inv.extra_recipients||"",
        });
        setLines(inv.items.map(it => ({...it, key:it.id, units:String(it.units), days:String(it.days), unit_price:String(it.unit_price), tax_rate:String(it.tax_rate)})));
      }).catch(console.error);
      api.get(`/payments/invoice/${id}`).then(setPayments).catch(console.error);
    } else {
      setForm(p => ({
        ...p,
        header_text: settings[`${docType}_header`]||"",
        intro_text:  settings[`${docType}_intro`] ||"",
        closing_text:settings[`${docType}_closing`]||"",
        footer_text: settings[`${docType}_footer`]||"",
        agb_text:    settings.agb_text||"",
        terms:       settings[`${docType}_terms`] ||"",
      }));
    }
  }, [id, isNew, docType, settings]);

  // Calculations
  const subtotal = lines.reduce((s,l) => s+Number(l.units||1)*Number(l.days||1)*Number(l.unit_price||0), 0);
  const taxPerItem = lines.reduce((s,l) => s+Number(l.units||1)*Number(l.days||1)*Number(l.unit_price||0)*Number(l.tax_rate||0), 0);
  const manualTaxVal = form.manual_tax!==""?Number(form.manual_tax):null;
  const taxTotal = manualTaxVal!==null?manualTaxVal:taxPerItem;
  const prodFee = subtotal*Number(form.production_fee_pct||0)/100;
  const discAmt = form.discount_type==="fixed"?Number(form.discount_value||0)
                : form.discount_type==="percent"?subtotal*Number(form.discount_value||0)/100 : 0;
  const grandTotal = subtotal+taxTotal+prodFee-discAmt;
  const amtPaid = payments.reduce((s,p) => s+Number(p.amount), 0);

  function setLine(key, field, value) {
    setLines(ls => ls.map(l => {
      if (l.key !== key) return l;
      const u = {...l, [field]:value};
      if (field === "inventory_id") {
        const item = inventory.find(i => i.id === value);
        if (item) { u.description=item.name; u.unit_price=String(item.day_rate); u.tax_rate=String(item.tax_rate||settings.default_tax_rate||0.20); }
      }
      return u;
    }));
  }

  async function save(andSend=false) {
    setError(""); setSaving(true);
    try {
      const body = {
        ...form, doc_type:docType,
        items: lines.map((l,i) => ({
          inventory_id:l.inventory_id||null, description:l.description,
          units:Number(l.units||1), days:Number(l.days||1),
          unit_price:Number(l.unit_price||0), tax_rate:Number(l.tax_rate||0),
          sort_order:i,
        })),
        manual_tax: form.manual_tax!==""?Number(form.manual_tax):null,
      };
      let inv;
      if (isNew) inv = await api.post("/invoices", body);
      else { await api.put(`/invoices/${id}`, body); inv = { id }; }
      if (andSend) {
        await api.post(`/invoices/${inv.id}/send`, { extra_email: extraEmail });
        alert("✓ "+t("sendEmail"));
      }
      navigate(`/${docType}s/${inv.id}`);
    } catch(e) { setError(e.message); }
    finally { setSaving(false); }
  }

  async function sendNow() {
    try {
      const r = await api.post(`/invoices/${id}/send`, { extra_email: extraEmail });
      setSendModal(false); setExtraEmail("");
      alert("✓ "+t("sent")+":\n"+(r.recipients||[]).join(", "));
      api.get(`/invoices/${id}`).then(setDoc);
    } catch(e) { alert(e.message); }
  }

  async function markPaid() {
    if (!window.confirm(t("markAsPaid")+"?")) return;
    await api.post(`/invoices/${id}/mark-paid`, {});
    api.get(`/invoices/${id}`).then(setDoc);
  }

  async function convertToInvoice() {
    if (!window.confirm(t("convertToInvoice")+"?")) return;
    const inv = await api.post(`/invoices/${id}/convert`, {});
    navigate(`/invoices/${inv.id}`);
  }

  async function recordPayment() {
    try {
      await api.post("/payments", { invoice_id:id, ...payForm, amount:Number(payForm.amount) });
      setPayModal(false);
      api.get(`/payments/invoice/${id}`).then(setPayments);
      api.get(`/invoices/${id}`).then(setDoc);
    } catch(e) { alert(e.message); }
  }

  async function sendReminder(level) {
    try {
      await api.post(`/invoices/${id}/reminder`, { level, extra_email:extraEmail });
      setReminderModal(false); setExtraEmail("");
      alert("✓ "+t("sent"));
    } catch(e) { alert(e.message); }
  }

  function loadTemplate(field) {
    const ts = templates.filter(tp => tp.type===field || (field==="notes_text"&&tp.type==="notes") || (field==="agb_text"&&tp.type==="agb"));
    if (!ts.length) return alert("Keine Vorlagen vorhanden");
    const choice = window.prompt("Vorlage:\n"+ts.map((tp,i)=>`${i+1}. ${tp.name}`).join("\n"));
    if (!choice) return;
    const idx = parseInt(choice)-1;
    if (ts[idx]) setForm(p => ({...p, [field]: ts[idx].content}));
  }

  const f = k => e => setForm(p => ({...p, [k]: e.target.type==="checkbox"?e.target.checked:e.target.value}));
  const canEdit = !doc || doc.status==="draft";
  const isInvoice = docType==="invoice";
  const backPath = `/${docType}s`;
  const title = isNew ? (isInvoice?t("newInvoice"):t("newQuote")) : doc?.doc_no||"";

  // Field with template loader
  const fieldT = (label, key, type) => (
    <div className="form-group">
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center" }}>
        <label>{label}</label>
        <button type="button" className="btn btn-ghost btn-sm" style={{ fontSize:10, padding:"2px 6px" }}
          onClick={() => loadTemplate(key)} disabled={!canEdit}>
          ↥ {t("loadTemplate")}
        </button>
      </div>
      <textarea value={form[key]||""} onChange={f(key)} disabled={!canEdit} />
    </div>
  );

  return (
    <div className="page">
      <div className="page-header">
        <button className="btn btn-ghost btn-sm" onClick={() => navigate(backPath)}>← {t("back")}</button>
        <h2>{title.toString().toUpperCase()}</h2>
        <div className="flex gap-2 ml-auto" style={{ flexWrap:"wrap" }}>
          {!isNew && isInvoice && doc && doc.status!=="paid" && (
            <button className="btn btn-secondary" onClick={markPaid}>{t("markAsPaid")}</button>
          )}
          {!isNew && isInvoice && doc && doc.status!=="paid" && (
            <button className="btn btn-secondary" onClick={() => setReminderModal(true)}>{t("sendReminder")}</button>
          )}
          {!isNew && isInvoice && doc && (
            <button className="btn btn-secondary" onClick={() => setPayModal(true)}>{t("recordPayment")}</button>
          )}
          {!isNew && !isInvoice && doc && doc.status!=="accepted" && (
            <button className="btn btn-secondary" onClick={convertToInvoice}>{t("convertToInvoice")}</button>
          )}
          {/* Save and Send buttons next to each other */}
          <button className="btn btn-secondary" onClick={() => isNew ? save(true) : setSendModal(true)} disabled={saving}>
            {saving ? t("saving") : t("send")} →
          </button>
          <button className="btn btn-primary" onClick={() => save()} disabled={saving}>
            {saving ? t("saving") : isInvoice ? t("saveInvoice") : t("saveQuote")}
          </button>
        </div>
      </div>

      {error && <div className="login-error" style={{ marginBottom:16 }}>{error}</div>}

      {doc && (
        <div style={{ display:"flex", gap:12, marginBottom:16, alignItems:"center" }}>
          <span className={`badge badge-${doc.status}`}>{doc.status.toUpperCase()}</span>
          {amtPaid > 0 && <span className="text-green" style={{ fontSize:13 }}>{t("paid")}: {fmt(amtPaid)}</span>}
        </div>
      )}

      {/* Subject + Language + Layout */}
      <div className="card" style={{ marginBottom:16 }}>
        <div className="form-grid-3">
          <div className="form-group" style={{ gridColumn:"1 / 2" }}>
            <label>{t("subject")}</label>
            <input value={form.subject} onChange={f("subject")} placeholder="—" disabled={!canEdit} />
            <small className="text-dim" style={{ fontSize:11, marginTop:2 }}>
              Wird automatisch um die Belegnummer ergänzt (z.B. "RE-2026-1234 | {form.subject||"…"}")
            </small>
          </div>
          <div className="form-group">
            <label>{t("language")}</label>
            <select value={form.doc_language} onChange={f("doc_language")} disabled={!canEdit}>
              <option value="de">Deutsch</option>
              <option value="en">English</option>
            </select>
          </div>
          <div className="form-group">
            <label>{t("layout")}</label>
            <select value={form.doc_layout} onChange={f("doc_layout")} disabled={!canEdit}>
              {LAYOUTS.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
          </div>
        </div>
      </div>

      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16, marginBottom:16 }}>
        <div className="card">
          <h4 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:14, fontSize:14 }}>{t("customer").toUpperCase()}</h4>
          <div className="form-group">
            <label>{t("customer")} *</label>
            <select value={form.customer_id} onChange={f("customer_id")} disabled={!canEdit}>
              <option value="">— {t("customer")} —</option>
              {customers.map(c => <option key={c.id} value={c.id}>{c.name}{c.company_name?` (${c.company_name})`:""}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label>{t("extraRecipients")}</label>
            <input value={form.extra_recipients||""} onChange={f("extra_recipients")} placeholder="email1@example.com, email2@example.com" disabled={!canEdit} />
            <small className="text-dim" style={{ fontSize:11 }}>{t("extraRecipientsHint")}</small>
          </div>
        </div>
        <div className="card">
          <h4 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:14, fontSize:14 }}>DATUM</h4>
          <div className="form-grid">
            <div className="form-group">
              <label>{isInvoice?t("due"):t("validUntil")}</label>
              <input type="date" value={isInvoice?form.due_date:form.valid_until} onChange={isInvoice?f("due_date"):f("valid_until")} disabled={!canEdit} />
            </div>
            <div className="form-group">
              <label>{t("rentalStart")}</label>
              <input type="date" value={form.rental_start} onChange={f("rental_start")} disabled={!canEdit} />
            </div>
            <div className="form-group">
              <label>{t("rentalEnd")}</label>
              <input type="date" value={form.rental_end} onChange={f("rental_end")} disabled={!canEdit} />
            </div>
          </div>
        </div>
      </div>

      {/* Header text with template */}
      <div className="card" style={{ marginBottom:16 }}>
        {fieldT(t("headerText"), "header_text")}
      </div>

      {/* Intro */}
      <div className="card" style={{ marginBottom:16 }}>
        {fieldT(t("introText"), "intro_text")}
      </div>

      {/* Line items */}
      <div className="card" style={{ marginBottom:16 }}>
        <h4 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:14, fontSize:14 }}>{t("lineItems").toUpperCase()}</h4>
        <div style={{ overflowX:"auto" }}>
          <table className="line-items" style={{ minWidth:800 }}>
            <thead><tr>
              <th style={{ width:180 }}>{t("fromInventory")}</th>
              <th>{t("description")}</th>
              <th style={{ width:70 }}>{t("units")}</th>
              <th style={{ width:70 }}>{t("days")}</th>
              <th style={{ width:110 }}>{t("unitPrice")}</th>
              <th style={{ width:90 }}>{t("taxRate")}</th>
              <th style={{ width:110, textAlign:"right" }}>{t("lineTotal")}</th>
              <th style={{ width:36 }}></th>
            </tr></thead>
            <tbody>
              {lines.map(l => {
                const lt = Number(l.units||1)*Number(l.days||1)*Number(l.unit_price||0);
                return (
                  <tr key={l.key}>
                    <td>
                      <select value={l.inventory_id||""} onChange={e=>setLine(l.key,"inventory_id",e.target.value)} disabled={!canEdit} style={{ fontSize:12 }}>
                        <option value="">{t("custom")}</option>
                        {inventory.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}
                      </select>
                    </td>
                    <td><input value={l.description} onChange={e=>setLine(l.key,"description",e.target.value)} placeholder={t("description")} disabled={!canEdit} /></td>
                    <td><input type="number" value={l.units} onChange={e=>setLine(l.key,"units",e.target.value)} min="0" step="1" disabled={!canEdit} /></td>
                    <td><input type="number" value={l.days} onChange={e=>setLine(l.key,"days",e.target.value)} min="0" step="0.5" disabled={!canEdit} /></td>
                    <td><input type="number" value={l.unit_price} onChange={e=>setLine(l.key,"unit_price",e.target.value)} step="0.01" disabled={!canEdit} /></td>
                    <td><input type="number" value={l.tax_rate} onChange={e=>setLine(l.key,"tax_rate",e.target.value)} step="0.01" disabled={!canEdit||settings.tax_mode==="manual"} /></td>
                    <td className="mono" style={{ textAlign:"right" }}>{fmt(lt)}</td>
                    <td>{canEdit && <button className="btn btn-danger btn-icon btn-sm" onClick={()=>setLines(ls=>ls.filter(x=>x.key!==l.key))}>✕</button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {canEdit && <button className="btn btn-ghost btn-sm" style={{ marginTop:10 }} onClick={() => setLines(ls => [...ls, newLine(settings.default_tax_rate)])}>{t("addLine")}</button>}

        <div style={{ marginTop:20, borderTop:"1px solid var(--border)", paddingTop:16 }}>
          <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:16, marginBottom:12 }}>
            <div className="form-group">
              <label>{t("discountType")}</label>
              <select value={form.discount_type} onChange={f("discount_type")} disabled={!canEdit}>
                <option value="none">{t("noDiscount")}</option>
                <option value="fixed">{t("fixedDiscount")}</option>
                <option value="percent">{t("percentDiscount")}</option>
              </select>
            </div>
            {form.discount_type !== "none" && (
              <div className="form-group">
                <label>{t("discountValue")}</label>
                <input type="number" step="0.01" value={form.discount_value} onChange={f("discount_value")} disabled={!canEdit} />
              </div>
            )}
            <div className="form-group">
              <label>{t("productionFee")}</label>
              <input type="number" step="0.1" value={form.production_fee_pct} onChange={f("production_fee_pct")} disabled={!canEdit} />
            </div>
            {settings.tax_mode === "manual" && (
              <div className="form-group">
                <label>{t("manualTax")}</label>
                <input type="number" step="0.01" value={form.manual_tax} onChange={f("manual_tax")} disabled={!canEdit} />
              </div>
            )}
          </div>

          <div style={{ maxWidth:320, marginLeft:"auto" }}>
            {[
              [t("subtotal"), fmt(subtotal)],
              [t("tax"), fmt(taxTotal)],
              ...(prodFee>0 ? [[`Production Fee (${form.production_fee_pct}%)`, fmt(prodFee)]] : []),
              ...(discAmt>0 ? [[t("discount"), `− ${fmt(discAmt)}`]] : []),
            ].map(([k,v]) => (
              <div key={k} style={{ display:"flex", justifyContent:"space-between", marginBottom:6, fontSize:13, color:"var(--text-dim)" }}>
                <span>{k}</span><span className="mono">{v}</span>
              </div>
            ))}
            <div style={{ display:"flex", justifyContent:"space-between", fontFamily:"var(--display)", fontSize:24, letterSpacing:1, marginTop:8, borderTop:"1px solid var(--border)", paddingTop:8 }}>
              <span>{t("grandTotal")}</span><span className="text-gold">{fmt(grandTotal)}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Closing, notes, terms, footer with template loaders */}
      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16, marginBottom:16 }}>
        <div className="card">{fieldT(t("closingText"), "closing_text")}</div>
        <div className="card">{fieldT(t("notes"), "notes_text")}</div>
        <div className="card">{fieldT(t("terms"), "terms")}</div>
        <div className="card">{fieldT(t("footerText"), "footer_text")}</div>
      </div>

      {/* AGB */}
      <div className="card" style={{ marginBottom:16 }}>
        <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:12 }}>
          <label style={{ display:"flex", alignItems:"center", gap:8 }}>
            <input type="checkbox" checked={form.include_agb} onChange={f("include_agb")} disabled={!canEdit} style={{ width:"auto" }} />
            <span style={{ textTransform:"none", fontSize:13 }}>{t("includeAgb")}</span>
          </label>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => loadTemplate("agb_text")} disabled={!canEdit}>↥ {t("loadTemplate")}</button>
        </div>
        {form.include_agb && <textarea value={form.agb_text||""} onChange={f("agb_text")} disabled={!canEdit} style={{ minHeight:120 }} />}
      </div>

      {/* Payment history */}
      {payments.length > 0 && (
        <div className="card" style={{ marginBottom:16 }}>
          <h4 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:14, fontSize:14 }}>{t("paymentHistory").toUpperCase()}</h4>
          <table style={{ width:"100%", borderCollapse:"collapse" }}>
            <thead><tr>
              {[t("date"),t("method"),t("reference"),t("amount")].map(h => (
                <th key={h} style={{ textAlign:h===t("amount")?"right":"left", padding:"6px 0", fontSize:11, color:"var(--text-dim)", textTransform:"uppercase" }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {payments.map(p => (
                <tr key={p.id} style={{ borderTop:"1px solid var(--border)" }}>
                  <td style={{ padding:"8px 0" }}>{new Date(p.paid_at).toLocaleDateString()}</td>
                  <td style={{ padding:"8px 0" }}>{p.method}</td>
                  <td style={{ padding:"8px 0", color:"var(--text-dim)" }}>{p.reference||"—"}</td>
                  <td style={{ padding:"8px 0", textAlign:"right" }} className="mono text-green">{fmt(p.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Send modal */}
      {sendModal && (
        <div className="modal-overlay" onClick={() => setSendModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{t("sendEmail").toUpperCase()}</h3>
              <button className="btn btn-ghost btn-icon" onClick={() => setSendModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <p style={{ fontSize:13, color:"var(--text-dim)", marginBottom:12 }}>
                Wird an alle <strong>primären</strong> und <strong>Rechnungs</strong>-E-Mails des Kunden gesendet.
              </p>
              <div className="form-group">
                <label>{t("extraRecipients")}</label>
                <input value={extraEmail} onChange={e=>setExtraEmail(e.target.value)} placeholder="zusatz@example.com" />
                <small className="text-dim" style={{ fontSize:11 }}>{t("extraRecipientsHint")}</small>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setSendModal(false)}>{t("cancel")}</button>
              <button className="btn btn-primary" onClick={sendNow}>{t("send")}</button>
            </div>
          </div>
        </div>
      )}

      {/* Pay modal */}
      {payModal && (
        <div className="modal-overlay" onClick={() => setPayModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{t("recordPayment").toUpperCase()}</h3>
              <button className="btn btn-ghost btn-icon" onClick={() => setPayModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="form-grid">
                <div className="form-group"><label>{t("amount")} *</label>
                  <input type="number" step="0.01" value={payForm.amount} onChange={e=>setPayForm(p=>({...p,amount:e.target.value}))} autoFocus />
                </div>
                <div className="form-group"><label>{t("date")}</label>
                  <input type="date" value={payForm.paid_at} onChange={e=>setPayForm(p=>({...p,paid_at:e.target.value}))} />
                </div>
                <div className="form-group"><label>{t("method")}</label>
                  <select value={payForm.method} onChange={e=>setPayForm(p=>({...p,method:e.target.value}))}>
                    <option>Überweisung</option><option>Bar</option><option>Karte</option><option>Scheck</option><option>Sonstiges</option>
                  </select>
                </div>
                <div className="form-group"><label>{t("reference")}</label>
                  <input value={payForm.reference} onChange={e=>setPayForm(p=>({...p,reference:e.target.value}))} />
                </div>
                <div className="form-group full"><label>{t("notes")}</label>
                  <textarea value={payForm.notes} onChange={e=>setPayForm(p=>({...p,notes:e.target.value}))} style={{ minHeight:60 }} />
                </div>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setPayModal(false)}>{t("cancel")}</button>
              <button className="btn btn-primary" onClick={recordPayment} disabled={!payForm.amount}>{t("addPayment")}</button>
            </div>
          </div>
        </div>
      )}

      {/* Reminder modal */}
      {reminderModal && (
        <div className="modal-overlay" onClick={() => setReminderModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{t("sendReminder").toUpperCase()}</h3>
              <button className="btn btn-ghost btn-icon" onClick={() => setReminderModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <p style={{ color:"var(--text-dim)", marginBottom:16, fontSize:13 }}>
                Mahngebühren: 1. {fmt(settings.reminder1_fee)}, 2. {fmt(settings.reminder2_fee)}, 3. {fmt(settings.reminder3_fee)}
              </p>
              <div className="form-group" style={{ marginBottom:16 }}>
                <label>{t("extraRecipients")}</label>
                <input value={extraEmail} onChange={e=>setExtraEmail(e.target.value)} placeholder="zusatz@example.com" />
              </div>
              <div style={{ display:"flex", flexDirection:"column", gap:10 }}>
                {[
                  [0, t("paymentReminder"), "btn-secondary"],
                  [1, t("dunning1"), "btn-danger"],
                  [2, t("dunning2"), "btn-danger"],
                  [3, t("dunning3"), "btn-danger"],
                ].map(([level, label, cls]) => (
                  <button key={level} className={`btn ${cls}`} onClick={() => sendReminder(level)}>{label}</button>
                ))}
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setReminderModal(false)}>{t("cancel")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
