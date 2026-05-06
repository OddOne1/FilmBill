import React from "react";
import { Outlet, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useI18n } from "../context/I18nContext";
import { useSettings } from "../context/SettingsContext";

const icons = {
  dashboard: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>,
  invoices:  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>,
  quotes:    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>,
  customers: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>,
  inventory: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>,
  users:     <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>,
  settings:  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>,
  logout:    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>,
};

export default function Layout() {
  const { user, logout } = useAuth();
  const { t } = useI18n();
  const { settings } = useSettings();
  const navigate = useNavigate();

  function handleLogout() { logout(); navigate("/login"); }

  const navItem = (to, label, icon) => (
    <NavLink key={to} to={to} className={({ isActive }) => "nav-item" + (isActive ? " active" : "")}>
      {icon}{label}
    </NavLink>
  );

  return (
    <div className="app-shell">
      <nav className="sidebar">
        <div className="sidebar-logo">
          {settings.company_logo
            ? <img src={settings.company_logo} alt="Logo" style={{ maxHeight: 40, maxWidth: 160, objectFit: "contain", marginBottom: 4 }} />
            : <h1>FILMBILL</h1>
          }
          <p>{settings.company_name || "Production Billing"}</p>
        </div>

        <div className="nav-section">
          <div className="nav-label">Main</div>
          {navItem("/dashboard", t("dashboard"), icons.dashboard)}
          {navItem("/invoices",  t("invoices"),  icons.invoices)}
          {navItem("/quotes",    t("quotes"),    icons.quotes)}
        </div>

        <div className="nav-section">
          <div className="nav-label">{t("customers")} & {t("inventory")}</div>
          {navItem("/customers", t("customers"), icons.customers)}
          {navItem("/inventory", t("inventory"), icons.inventory)}
        </div>

        <div className="nav-section">
          <div className="nav-label">System</div>
          {user?.role === "admin" && navItem("/users", t("users"), icons.users)}
          {navItem("/settings", t("settings"), icons.settings)}
        </div>

        <div className="sidebar-footer">
          <div className="user-chip">
            <div className="avatar">{user?.name?.[0]?.toUpperCase()}</div>
            <div className="info">
              <div className="name">{user?.name}</div>
              <div className="role">{user?.role === "admin" ? t("adminRole") : t("userRole")}</div>
            </div>
          </div>
          <button className="btn btn-ghost btn-sm" style={{ width: "100%", marginTop: 8 }} onClick={handleLogout}>
            {icons.logout} {t("signOut")}
          </button>
        </div>
      </nav>
      <main className="main"><Outlet /></main>
    </div>
  );
}
