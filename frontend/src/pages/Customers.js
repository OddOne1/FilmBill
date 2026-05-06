import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api";
import { useI18n } from "../context/I18nContext";
import ContactEntries from "../components/ContactEntries";

const EMPTY_C  = { company_id:"", name:"", notes:"", contacts:[] };
const EMPTY_CO = { name:"", vat:"", notes:"", contacts:[] };

export default function Customers() {
  const { t } = useI18n();
  const [tab, setTab] = useState("customers");
  const [customers, setCustomers] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [q, setQ] = useState("");
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState(EMPTY_C);
  const [coModal, setCoModal] = useState(null);
  const [coForm, setCoForm] = useState(EMPTY_CO);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const loadAll = useCallback(() => {
    api.get(`/customers${q?`?q=${encodeURIComponent(q)}`:""}`).then(setCustomers).catch(console.error);
    api.get("/companies").then(setCompanies).catch(console.error);
  }, [q]);

  useEffect(() => { loadAll(); }, [loadAll]);

  async function saveCustomer() {
    setError(""); setSaving(true);
    try {
      if (modal==="add") await api.post("/customers", form);
      else await api.put(`/customers/${form.id}`, form);
      setModal(null); loadAll();
    } catch(e) { setError(e.message); }
    finally { setSaving(false); }
  }

  async function saveCompany() {
    setError(""); setSaving(true);
    try {
      if (coModal==="add") await api.post("/companies", coForm);
      else await api.put(`/companies/${coForm.id}`, coForm);
      setCoModal(null); loadAll();
    } catch(e) { setError(e.message); }
    finally { setSaving(false); }
  }

  const f  = k => e => setForm(p=>({...p,[k]:e.target.value}));
  const cf = k => e => setCoForm(p=>({...p,[k]:e.target.value}));

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("customers").toUpperCase()}</h2>
        <div className="flex gap-2">
          <button className="btn btn-secondary" onClick={() => { setCoForm(EMPTY_CO); setError(""); setCoModal("add"); }}>{t("addCompany")}</button>
          <button className="btn btn-primary" onClick={() => { setForm(EMPTY_C); setError(""); setModal("add"); }}>{t("addCustomer")}</button>
        </div>
      </div>

      <div style={{ display:"flex", gap:4, marginBottom:16 }}>
        {["customers","companies"].map(tab2 => (
          <button key={tab2} className={`btn ${tab===tab2?"btn-primary":"btn-secondary"}`} onClick={() => setTab(tab2)}>
            {tab2==="customers" ? t("customers") : t("companies")}
          </button>
        ))}
      </div>

      <div className="search-bar">
        <input placeholder={t("search")+"…"} value={q} onChange={e => setQ(e.target.value)} />
      </div>

      {tab === "customers" && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>{t("name")}</th><th>{t("companies")}</th><th>{t("email")}</th><th>{t("phone")}</th><th></th></tr></thead>
            <tbody>
              {customers.length===0 && <tr><td colSpan={5} style={{ textAlign:"center",padding:"40px",color:"var(--text-dim)" }}>—</td></tr>}
              {customers.map(c => (
                <tr key={c.id}>
                  <td><strong>{c.name}</strong></td>
                  <td className="text-dim">{c.company_name||"—"}</td>
                  <td><a href={`mailto:${c.primary_email}`}>{c.primary_email||"—"}</a></td>
                  <td className="text-dim">{c.primary_phone||"—"}</td>
                  <td style={{ textAlign:"right" }}>
                    <button className="btn btn-ghost btn-sm" onClick={() => { setForm({...c, contacts: c.contacts||[]}); setError(""); setModal("edit"); }}>{t("edit")}</button>
                    <button className="btn btn-danger btn-sm" style={{ marginLeft:6 }} onClick={async()=>{ if(window.confirm(t("deleteCustomer"))){await api.delete(`/customers/${c.id}`);loadAll();}}}>{t("delete")}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "companies" && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>{t("name")}</th><th>{t("email")}</th><th>{t("vat")}</th><th>Kunden</th><th></th></tr></thead>
            <tbody>
              {companies.length===0 && <tr><td colSpan={5} style={{ textAlign:"center",padding:"40px",color:"var(--text-dim)" }}>—</td></tr>}
              {companies.map(co => (
                <tr key={co.id}>
                  <td><strong>{co.name}</strong></td>
                  <td className="text-dim">{co.primary_email||"—"}</td>
                  <td className="mono text-dim" style={{ fontSize:12 }}>{co.vat||"—"}</td>
                  <td>{co.customer_count}</td>
                  <td style={{ textAlign:"right" }}>
                    <button className="btn btn-ghost btn-sm" onClick={() => { setCoForm({...co, contacts: co.contacts||[]}); setError(""); setCoModal("edit"); }}>{t("edit")}</button>
                    <button className="btn btn-danger btn-sm" style={{ marginLeft:6 }} onClick={async()=>{ if(window.confirm(t("deleteCompany"))){await api.delete(`/companies/${co.id}`);loadAll();}}}>{t("delete")}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Customer modal */}
      {modal && (
        <div className="modal-overlay" onClick={() => setModal(null)}>
          <div className="modal modal-lg" onClick={e=>e.stopPropagation()}>
            <div className="modal-header">
              <h3>{modal==="add" ? t("addCustomer").toUpperCase() : t("edit").toUpperCase()}</h3>
              <button className="btn btn-ghost btn-icon" onClick={() => setModal(null)}>✕</button>
            </div>
            <div className="modal-body">
              {error && <div className="login-error">{error}</div>}
              <div className="form-grid">
                <div className="form-group"><label>{t("name")} *</label><input value={form.name} onChange={f("name")} required /></div>
                <div className="form-group"><label>{t("company")}</label>
                  <select value={form.company_id||""} onChange={f("company_id")}>
                    <option value="">— {t("none")} —</option>
                    {companies.map(co=><option key={co.id} value={co.id}>{co.name}</option>)}
                  </select>
                </div>
                <div className="form-group full"><label>{t("notes")}</label><textarea value={form.notes||""} onChange={f("notes")} /></div>
              </div>
              <hr className="divider" />
              <h4 style={{ fontSize:13, marginBottom:12, letterSpacing:1, color:"var(--text-dim)" }}>{t("contactsLabel").toUpperCase()}</h4>
              <ContactEntries contacts={form.contacts||[]} onChange={cs => setForm(p => ({...p, contacts:cs}))} />
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setModal(null)}>{t("cancel")}</button>
              <button className="btn btn-primary" onClick={saveCustomer} disabled={saving}>{saving?t("saving"):t("save")}</button>
            </div>
          </div>
        </div>
      )}

      {/* Company modal */}
      {coModal && (
        <div className="modal-overlay" onClick={() => setCoModal(null)}>
          <div className="modal modal-lg" onClick={e=>e.stopPropagation()}>
            <div className="modal-header">
              <h3>{coModal==="add" ? t("addCompany").toUpperCase() : t("edit").toUpperCase()}</h3>
              <button className="btn btn-ghost btn-icon" onClick={() => setCoModal(null)}>✕</button>
            </div>
            <div className="modal-body">
              {error && <div className="login-error">{error}</div>}
              <div className="form-grid">
                <div className="form-group"><label>{t("name")} *</label><input value={coForm.name} onChange={cf("name")} required /></div>
                <div className="form-group"><label>{t("vat")}</label><input value={coForm.vat||""} onChange={cf("vat")} /></div>
                <div className="form-group full"><label>{t("notes")}</label><textarea value={coForm.notes||""} onChange={cf("notes")} /></div>
              </div>
              <hr className="divider" />
              <h4 style={{ fontSize:13, marginBottom:12, letterSpacing:1, color:"var(--text-dim)" }}>{t("contactsLabel").toUpperCase()}</h4>
              <ContactEntries contacts={coForm.contacts||[]} onChange={cs => setCoForm(p => ({...p, contacts:cs}))} />
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setCoModal(null)}>{t("cancel")}</button>
              <button className="btn btn-primary" onClick={saveCompany} disabled={saving}>{saving?t("saving"):t("save")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
