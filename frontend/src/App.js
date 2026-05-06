import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { I18nProvider } from "./context/I18nContext";
import { SettingsProvider } from "./context/SettingsContext";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import ResetPassword from "./pages/ResetPassword";
import Dashboard from "./pages/Dashboard";
import Invoices from "./pages/Invoices";
import InvoiceEditor from "./pages/InvoiceEditor";
import Customers from "./pages/Customers";
import Inventory from "./pages/Inventory";
import Users from "./pages/Users";
import Settings from "./pages/Settings";

function Private({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="login-wrap"><p style={{color:"var(--text-dim)"}}>Loading…</p></div>;
  return user ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <I18nProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/reset-password" element={<ResetPassword />} />
            <Route path="/" element={<Private><SettingsProvider><Layout /></SettingsProvider></Private>}>
              <Route index element={<Navigate to="/dashboard" replace />} />
              <Route path="dashboard"      element={<Dashboard />} />
              <Route path="invoices"       element={<Invoices docType="invoice" />} />
              <Route path="invoices/new"   element={<InvoiceEditor docType="invoice" />} />
              <Route path="invoices/:id"   element={<InvoiceEditor docType="invoice" />} />
              <Route path="quotes"         element={<Invoices docType="quote" />} />
              <Route path="quotes/new"     element={<InvoiceEditor docType="quote" />} />
              <Route path="quotes/:id"     element={<InvoiceEditor docType="quote" />} />
              <Route path="customers"      element={<Customers />} />
              <Route path="inventory"      element={<Inventory />} />
              <Route path="users"          element={<Users />} />
              <Route path="settings"       element={<Settings />} />
            </Route>
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </I18nProvider>
  );
}
