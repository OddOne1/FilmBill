import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { useI18n } from "../context/I18nContext";
import { api } from "../api";

export default function Login() {
  const { login } = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [forgotMode, setForgotMode] = useState(false);
  const [forgotSent, setForgotSent] = useState(false);

  async function handleLogin(e) {
    e.preventDefault(); setError(""); setLoading(true);
    try { await login(email, password); navigate("/dashboard"); }
    catch(err) { setError(t("invalidCredentials")); }
    finally { setLoading(false); }
  }

  async function handleForgot(e) {
    e.preventDefault(); setLoading(true);
    try { await api.post("/auth/forgot-password", { email }); setForgotSent(true); }
    catch { setForgotSent(true); }
    finally { setLoading(false); }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-header"><h1>FILMBILL</h1><p>Production · Equipment · Billing</p></div>
        <div className="login-body">
          {!forgotMode ? (
            <>
              <h3 style={{ marginBottom:20,fontFamily:"var(--display)",fontSize:18,letterSpacing:2 }}>{t("signIn").toUpperCase()}</h3>
              {error && <div className="login-error">{error}</div>}
              <form onSubmit={handleLogin}>
                <div className="form-group" style={{ marginBottom:12 }}>
                  <label>{t("email")}</label>
                  <input type="email" value={email} onChange={e=>setEmail(e.target.value)} required autoFocus />
                </div>
                <div className="form-group" style={{ marginBottom:20 }}>
                  <label>{t("password")}</label>
                  <input type="password" value={password} onChange={e=>setPassword(e.target.value)} required />
                </div>
                <button className="btn btn-primary" style={{ width:"100%" }} disabled={loading}>
                  {loading ? t("loading") : t("signIn")}
                </button>
              </form>
              <div style={{ textAlign:"center",marginTop:16 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setForgotMode(true)}>{t("forgotPassword")}</button>
              </div>
            </>
          ) : (
            <>
              <h3 style={{ marginBottom:12,fontFamily:"var(--display)",fontSize:18,letterSpacing:2 }}>{t("resetPassword").toUpperCase()}</h3>
              {forgotSent
                ? <p style={{ color:"var(--text-dim)",fontSize:13,marginBottom:16 }}>Reset-Link wurde gesendet.</p>
                : <form onSubmit={handleForgot}>
                    <div className="form-group" style={{ marginBottom:16 }}>
                      <label>{t("email")}</label>
                      <input type="email" value={email} onChange={e=>setEmail(e.target.value)} required autoFocus />
                    </div>
                    <button className="btn btn-primary" style={{ width:"100%" }} disabled={loading}>
                      {loading ? t("loading") : t("sendResetLink")}
                    </button>
                  </form>
              }
              <div style={{ textAlign:"center",marginTop:16 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => { setForgotMode(false); setForgotSent(false); }}>{t("backToLogin")}</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
