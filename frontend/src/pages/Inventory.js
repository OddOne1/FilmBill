import React, { useEffect, useState, useCallback } from "react";
import { api } from "../api";
import { useI18n } from "../context/I18nContext";
import { useSettings } from "../context/SettingsContext";

const EMPTY = { name:"",sku:"",category:"Sonstiges",description:"",day_rate:"",week_rate:"",sale_price:"",tax_rate:"0.20",unit:"Tag",stock:"1",active:true,notes:"" };

export default function Inventory() {
  const { t } = useI18n();
  const { fmt, settings } = useSettings();
  const [items, setItems] = useState([]);
  const [categories, setCategories] = useState([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({...EMPTY});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    let path = "/inventory?active=true";
    if (q) path += `&q=${encodeURIComponent(q)}`;
    if (cat) path += `&category=${cat}`;
    api.get(path).then(setItems).catch(console.error);
    api.get("/settings/categories").then(setCategories).catch(console.error);
  }, [q, cat]);

  useEffect(() => { load(); }, [load]);

  async function save() {
    setError(""); setSaving(true);
    try {
      if (modal==="add") await api.post("/inventory", form);
      else await api.put(`/inventory/${form.id}`, form);
      setModal(null); load();
    } catch(e) { setError(e.message); }
    finally { setSaving(false); }
  }

  const f = k => e => setForm(p=>({...p,[k]:e.target.type==="checkbox"?e.target.checked:e.target.value}));

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("inventory").toUpperCase()}</h2>
        <button className="btn btn-primary" onClick={() => { setForm({...EMPTY, tax_rate: settings.default_tax_rate||"0.20"}); setError(""); setModal("add"); }}>{t("addItem")}</button>
      </div>
      <div className="search-bar">
        <input placeholder={t("search")+"…"} value={q} onChange={e=>setQ(e.target.value)} style={{ maxWidth:280 }} />
        <select value={cat} onChange={e=>setCat(e.target.value)} style={{ maxWidth:200 }}>
          <option value="">{t("category")}: {t("allStatuses").replace("Status","")}</option>
          {categories.map(c=><option key={c.id} value={c.name}>{c.name}</option>)}
        </select>
        <span className="text-dim" style={{ fontSize:12, marginLeft:"auto", alignSelf:"center" }}>
          → {t("settings")} → {t("manageCategories")}
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr>
            <th>{t("name")}</th><th>{t("sku")}</th><th>{t("category")}</th>
            <th>{t("dayRate")}</th><th>{t("weekRate")}</th><th>{t("stock")}</th><th>{t("taxRate")}</th><th></th>
          </tr></thead>
          <tbody>
            {items.length===0 && <tr><td colSpan={8} style={{ textAlign:"center",padding:"40px",color:"var(--text-dim)" }}>—</td></tr>}
            {items.map(item => (
              <tr key={item.id}>
                <td>
                  <strong>{item.name}</strong>
                  {item.description && <div style={{ fontSize:12,color:"var(--text-dim)",marginTop:2 }}>{item.description.slice(0,60)}</div>}
                </td>
                <td className="mono text-dim" style={{ fontSize:12 }}>{item.sku||"—"}</td>
                <td><span className="badge badge-draft">{item.category}</span></td>
                <td className="text-gold mono">{fmt(item.day_rate)}</td>
                <td className="mono text-dim">{item.week_rate ? fmt(item.week_rate) : "—"}</td>
                <td>{item.stock}</td>
                <td className="text-dim">{(Number(item.tax_rate)*100).toFixed(0)}%</td>
                <td style={{ textAlign:"right" }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => { setForm({...item,week_rate:item.week_rate||"",sale_price:item.sale_price||""}); setError(""); setModal("edit"); }}>{t("edit")}</button>
                  <button className="btn btn-danger btn-sm" style={{ marginLeft:6 }} onClick={async()=>{ if(window.confirm(t("archiveItem"))){await api.delete(`/inventory/${item.id}`);load();}}}>{t("delete")}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && (
        <div className="modal-overlay" onClick={() => setModal(null)}>
          <div className="modal" onClick={e=>e.stopPropagation()}>
            <div className="modal-header">
              <h3>{modal==="add" ? t("addItem").toUpperCase() : t("edit").toUpperCase()}</h3>
              <button className="btn btn-ghost btn-icon" onClick={() => setModal(null)}>✕</button>
            </div>
            <div className="modal-body">
              {error && <div className="login-error">{error}</div>}
              <div className="form-grid">
                <div className="form-group"><label>{t("name")} *</label><input value={form.name} onChange={f("name")} required /></div>
                <div className="form-group"><label>{t("sku")}</label><input value={form.sku||""} onChange={f("sku")} /></div>
                <div className="form-group"><label>{t("category")}</label>
                  <select value={form.category} onChange={f("category")}>
                    {categories.map(c=><option key={c.id} value={c.name}>{c.name}</option>)}
                  </select>
                </div>
                <div className="form-group"><label>{t("unit")}</label>
                  <select value={form.unit} onChange={f("unit")}>
                    {["Tag","Woche","Stunde","Stück","Pausch."].map(u=><option key={u}>{u}</option>)}
                  </select>
                </div>
                <div className="form-group"><label>{t("dayRate")}</label><input type="number" step="0.01" value={form.day_rate} onChange={f("day_rate")} /></div>
                <div className="form-group"><label>{t("weekRate")}</label><input type="number" step="0.01" value={form.week_rate||""} onChange={f("week_rate")} /></div>
                <div className="form-group"><label>{t("salePrice")}</label><input type="number" step="0.01" value={form.sale_price||""} onChange={f("sale_price")} /></div>
                <div className="form-group"><label>{t("taxRate")} (z.B. 0.20)</label><input type="number" step="0.01" value={form.tax_rate} onChange={f("tax_rate")} /></div>
                <div className="form-group"><label>{t("stock")}</label><input type="number" value={form.stock} onChange={f("stock")} /></div>
                <div className="form-group full"><label>{t("description")}</label><textarea value={form.description||""} onChange={f("description")} /></div>
                <div className="form-group full"><label>{t("notes")}</label><textarea value={form.notes||""} onChange={f("notes")} style={{ minHeight:60 }} /></div>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setModal(null)}>{t("cancel")}</button>
              <button className="btn btn-primary" onClick={save} disabled={saving}>{saving?t("saving"):t("save")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
