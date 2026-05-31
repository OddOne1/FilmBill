import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useI18n } from "../context/I18nContext";
import { useSettings } from "../context/SettingsContext";
import { useAuth } from "../context/AuthContext";
import { api } from "../api";

export default function Dashboard() {
  const [data, setData] = useState(null);
  const { t } = useI18n();
  const { fmt } = useSettings();
  const { user } = useAuth();

  useEffect(() => { api.get("/dashboard").then(setData).catch(console.error); }, []);

  const byStatus = data?.byStatus || [];
  const getInv = s => byStatus.find(r => r.status===s && r.doc_type==='invoice');
  const totalRev = byStatus.filter(r=>r.doc_type==='invoice').reduce((a,r)=>a+Number(r.total||0),0);
  const outstanding = Number(getInv("sent")?.total||0) + Number(getInv("partial")?.total||0);

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("dashboard").toUpperCase()}</h2>
        <div className="flex gap-2">
          <Link to="/invoices/new" className="btn btn-primary">{t("newInvoice")}</Link>
          <Link to="/quotes/new" className="btn btn-secondary">{t("newQuote")}</Link>
        </div>
      </div>

      <div className="stat-grid">
        {[
          [t("totalRevenue"), fmt(totalRev), t("allTime"), "var(--gold)"],
          [t("outstanding"), fmt(outstanding), t("sentPartial"), "var(--blue)"],
          [t("paidInvoices"), getInv("paid")?.count||0, fmt(getInv("paid")?.total||0)+" "+t("collected"), "var(--green)"],
          [t("overdue"), data?.overdueCount||0, t("requireAttention"), "var(--red)"],
          [t("drafts"), getInv("draft")?.count||0, t("notYetSent"), "var(--text)"],
        ].map(([label,value,sub,color]) => (
          <div key={label} className="stat-card">
            <div className="label">{label}</div>
            <div className="value" style={{ color }}>{value}</div>
            <div className="sub">{sub}</div>
          </div>
        ))}
      </div>

      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:20, marginBottom:20 }}>
        <div className="card">
          <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:16, fontSize:16 }}>{t("recentInvoices").toUpperCase()}</h3>
          {!(data?.recentInvoices?.length)
            ? <p className="text-dim" style={{ fontSize:13 }}>{t("noInvoicesYet")}</p>
            : <div className="table-wrap">
                <table>
                  <thead><tr>
                    <th>{t("docNo")}</th>
                    <th>{t("customer")}</th>
                    <th>{t("total")}</th>
                    <th>{t("status")}</th>
                  </tr></thead>
                  <tbody>
                    {(data.recentInvoices).map(d => (
                      <tr key={d.id}>
                        <td className="mono text-gold" style={{ fontSize:12 }}>{d.doc_no}</td>
                        <td style={{ fontSize:13 }}>{d.customer_name}</td>
                        <td className="mono" style={{ fontSize:13 }}>{fmt(d.total)}</td>
                        <td><span className={`badge badge-${d.status}`}>{d.status}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
          }
        </div>
        <div className="card">
          <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:16, fontSize:16 }}>{t("topItems").toUpperCase()}</h3>
          {(data?.topItems||[]).length === 0
            ? <p className="text-dim" style={{ fontSize:13 }}>{t("noDataYet")}</p>
            : (data?.topItems||[]).map((item,i) => (
              <div key={i} style={{ display:"flex", justifyContent:"space-between", borderTop:"1px solid var(--border)", padding:"8px 0", fontSize:13 }}>
                <span>{item.description}</span>
                <span className="text-gold mono">{fmt(item.revenue)}</span>
              </div>
            ))
          }
        </div>
      </div>

      {user?.role === "admin" && (data?.userStats||[]).length > 0 && (
        <div className="card">
          <h3 style={{ fontFamily:"var(--display)", letterSpacing:2, marginBottom:16, fontSize:16 }}>
            {t("userStats").toUpperCase()} {data?.year}
          </h3>
          <div className="table-wrap">
            <table>
              <thead><tr>
                <th>{t("name")}</th>
                <th>{t("invoiceCount")}</th>
                <th>{t("total")} ({t("invoices")})</th>
                <th>{t("quoteCount")}</th>
                <th>{t("total")} ({t("quotes")})</th>
              </tr></thead>
              <tbody>
                {(data?.userStats||[]).map(u => (
                  <tr key={u.id}>
                    <td><strong>{u.name}</strong></td>
                    <td>{u.invoice_count}</td>
                    <td className="mono text-gold">{fmt(u.invoice_total)}</td>
                    <td>{u.quote_count}</td>
                    <td className="mono text-dim">{fmt(u.quote_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
