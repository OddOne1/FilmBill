import React, { useEffect, useState, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useI18n } from "../context/I18nContext";
import { useSettings } from "../context/SettingsContext";

const STATUSES = ["","draft","sent","accepted","declined","partial","paid","overdue","cancelled"];

export default function Invoices({ docType = "invoice" }) {
  const [docs, setDocs] = useState([]);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const { t } = useI18n();
  const { fmt } = useSettings();
  const navigate = useNavigate();
  const isInvoice = docType === "invoice";

  const load = useCallback(() => {
    let path = `/invoices?doc_type=${docType}`;
    if (q) path += `&q=${encodeURIComponent(q)}`;
    if (status) path += `&status=${status}`;
    api.get(path).then(setDocs).catch(console.error);
  }, [q, status, docType]);

  useEffect(() => { load(); }, [load]);

  async function send(id, e) {
    e.stopPropagation();
    if (!window.confirm(t("sendEmail") + "?")) return;
    try { await api.post(`/invoices/${id}/send`, {}); load(); alert("✓"); }
    catch(err) { alert(err.message); }
  }

  return (
    <div className="page">
      <div className="page-header">
        <h2>{isInvoice ? t("invoices").toUpperCase() : t("quotes").toUpperCase()}</h2>
        <Link to={`/${docType}s/new`} className="btn btn-primary">
          {isInvoice ? t("newInvoice") : t("newQuote")}
        </Link>
      </div>

      <div className="search-bar">
        <input placeholder={t("search") + "…"} value={q} onChange={e => setQ(e.target.value)} />
        <select value={status} onChange={e => setStatus(e.target.value)} style={{ maxWidth:160 }}>
          <option value="">{t("allStatuses")}</option>
          {STATUSES.filter(Boolean).map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <div className="table-wrap">
        <table>
          <thead><tr>
            <th>{t("docNo")}</th>
            <th>{t("subject")}</th>
            <th>{t("customer")}</th>
            <th>{isInvoice ? t("due") : t("validUntil")}</th>
            <th>{t("total")}</th>
            {isInvoice && <th>{t("paid")}</th>}
            <th>{t("status")}</th>
            <th></th>
          </tr></thead>
          <tbody>
            {docs.length === 0 && (
              <tr><td colSpan={8} style={{ textAlign:"center", padding:"40px", color:"var(--text-dim)" }}>
                {t("noInvoicesYet")}
              </td></tr>
            )}
            {docs.map(d => (
              <tr key={d.id} style={{ cursor:"pointer" }} onClick={() => navigate(`/${docType}s/${d.id}`)}>
                <td className="mono text-gold" style={{ fontSize:13 }}>{d.doc_no}</td>
                <td style={{ color:"var(--text-dim)", fontSize:13 }}>{d.subject||"—"}</td>
                <td>
                  <div>{d.customer_name}</div>
                  <div style={{ fontSize:12, color:"var(--text-dim)" }}>{d.customer_email}</div>
                </td>
                <td className={isInvoice && new Date(d.due_date)<new Date() && d.status!=="paid" ? "text-red":"text-dim"}>
                  {d.due_date ? new Date(d.due_date).toLocaleDateString() : "—"}
                </td>
                <td className="mono">{fmt(d.total)}</td>
                {isInvoice && <td className="mono text-dim">{fmt(d.amount_paid)}</td>}
                <td><span className={`badge badge-${d.status}`}>{d.status}</span></td>
                <td onClick={e => e.stopPropagation()} style={{ textAlign:"right" }}>
                  {d.status === "draft" && (
                    <button className="btn btn-secondary btn-sm" onClick={e => send(d.id, e)}>{t("sendEmail")}</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
