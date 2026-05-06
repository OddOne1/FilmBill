import React from "react";
import { useI18n } from "../context/I18nContext";

// Reusable contact entries editor for both customers and companies
export default function ContactEntries({ contacts = [], onChange }) {
  const { t } = useI18n();

  function update(idx, field, value) {
    const copy = [...contacts];
    copy[idx] = { ...copy[idx], [field]: value };
    onChange(copy);
  }
  function add(type = "email") {
    onChange([...contacts, { type, label: t("labelWork"), value: "", is_primary: contacts.length===0, is_billing:false, is_delivery:false }]);
  }
  function remove(idx) {
    onChange(contacts.filter((_, i) => i !== idx));
  }
  function setPrimary(idx, type) {
    onChange(contacts.map((c, i) => c.type === type ? { ...c, is_primary: i === idx } : c));
  }

  return (
    <div>
      <div style={{ marginBottom: 12, display:"flex", gap: 6 }}>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => add("email")}>+ E-Mail</button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => add("phone")}>+ {t("phone")}</button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => add("address")}>+ {t("address")}</button>
      </div>
      {contacts.length === 0 && (
        <p className="text-dim" style={{ fontSize:12 }}>—</p>
      )}
      {contacts.map((c, i) => (
        <div key={i} style={{ display:"grid", gridTemplateColumns:"100px 130px 1fr auto", gap:8, marginBottom:8, alignItems:"center" }}>
          <select value={c.type} onChange={e => update(i, "type", e.target.value)} style={{ fontSize:12, padding:"6px 8px" }}>
            <option value="email">E-Mail</option>
            <option value="phone">{t("phone")}</option>
            <option value="address">{t("address")}</option>
          </select>
          <select value={c.label} onChange={e => update(i, "label", e.target.value)} style={{ fontSize:12, padding:"6px 8px" }}>
            <option value={t("labelWork")}>{t("labelWork")}</option>
            <option value={t("labelPrivate")}>{t("labelPrivate")}</option>
            <option value={t("labelBilling")}>{t("labelBilling")}</option>
            <option value={t("labelDelivery")}>{t("labelDelivery")}</option>
            <option value={t("labelOther")}>{t("labelOther")}</option>
          </select>
          {c.type === "address"
            ? <textarea value={c.value} onChange={e => update(i, "value", e.target.value)} placeholder={t("address")} style={{ minHeight: 50, fontSize:12, padding:"6px 8px" }} />
            : <input value={c.value} onChange={e => update(i, "value", e.target.value)} placeholder={c.type === "email" ? "name@example.com" : "+43 1 234 5678"} style={{ fontSize:12, padding:"6px 8px" }} />
          }
          <div style={{ display:"flex", gap:4, fontSize:11 }}>
            <label style={{ cursor:"pointer", display:"flex", alignItems:"center", gap:2 }}>
              <input type="radio" name={`primary-${c.type}`} checked={c.is_primary} onChange={() => setPrimary(i, c.type)} />
              <span>{t("primary")}</span>
            </label>
            {c.type === "email" && (
              <label style={{ cursor:"pointer", display:"flex", alignItems:"center", gap:2 }}>
                <input type="checkbox" checked={c.is_billing} onChange={e => update(i, "is_billing", e.target.checked)} />
                <span>{t("billing")}</span>
              </label>
            )}
            <button type="button" className="btn btn-danger btn-icon btn-sm" onClick={() => remove(i)}>✕</button>
          </div>
        </div>
      ))}
    </div>
  );
}
