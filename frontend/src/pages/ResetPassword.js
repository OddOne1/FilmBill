import React, { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handle(e) {
    e.preventDefault();
    if (password !== confirm) return setError("Passwords do not match");
    if (password.length < 8) return setError("Minimum 8 characters");
    setLoading(true); setError("");
    try {
      await api.post("/auth/reset-password", { token, password });
      navigate("/login");
    } catch (err) { setError(err.message); }
    finally { setLoading(false); }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-header">
          <h1>FILMBILL</h1>
          <p>Password Reset</p>
        </div>
        <div className="login-body">
          {error && <div className="login-error">{error}</div>}
          <form onSubmit={handle}>
            <div className="form-group" style={{ marginBottom: 12 }}>
              <label>New Password</label>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)} required autoFocus />
            </div>
            <div className="form-group" style={{ marginBottom: 20 }}>
              <label>Confirm Password</label>
              <input type="password" value={confirm} onChange={e => setConfirm(e.target.value)} required />
            </div>
            <button className="btn btn-primary" style={{ width: "100%" }} disabled={loading}>
              {loading ? "Saving…" : "Set New Password"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
