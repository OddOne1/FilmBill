import React, { useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../context/AuthContext";
import { useI18n } from "../context/I18nContext";

export default function Users() {
  const { user: me } = useAuth();
  const { t } = useI18n();
  const [users, setUsers] = useState([]);
  const [modal, setModal] = useState(false);
  const [editModal, setEditModal] = useState(null);
  const [form, setForm] = useState({ email:"", name:"", role:"user" });
  const [editForm, setEditForm] = useState({ name:"", email:"", role:"user" });
  const [pwModal, setPwModal] = useState(false);
  const [pwForm, setPwForm] = useState({ currentPassword:"", newPassword:"", confirmPassword:"" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { load(); }, []);
  function load() { api.get("/users").then(setUsers).catch(console.error); }

  async function invite() {
    setError(""); setSaving(true);
    try { await api.post("/users", form); setModal(false); setForm({ email:"",name:"",role:"user" }); load(); }
    catch(e) { setError(e.message); }
    finally { setSaving(false); }
  }

  async function saveEdit() {
    setError(""); setSaving(true);
    try { await api.put(`/users/${editModal.id}`, editForm); setEditModal(null); load(); }
    catch(e) { setError(e.message); }
    finally { setSaving(false); }
  }

  async function toggleActive(u) {
    await api.put(`/users/${u.id}`, { ...u, active: !u.active }); load();
  }

  async function changePassword() {
    if (pwForm.newPassword !== pwForm.confirmPassword) return setError(t("confirmPassword")+" stimmt nicht überein");
    if (pwForm.newPassword.length < 8) return setError("Min. 8 Zeichen");
    setError(""); setSaving(true);
    try { await api.post("/auth/change-password", { currentPassword:pwForm.currentPassword, newPassword:pwForm.newPassword }); setPwModal(false); alert("✓"); }
    catch(e) { setError(e.message); }
    finally { setSaving(false); }
  }

  const f  = k => e => setForm(p=>({...p,[k]:e.target.value}));
  const ef = k => e => setEditForm(p=>({...p,[k]:e.target.value}));
  const pf = k => e => setPwForm(p=>({...p,[k]:e.target.value}));

  return (
    <div className="page">
      <div className="page-header">
        <h2>{t("users").toUpperCase()}</h2>
        <div className="flex gap-2">
          <button className="btn btn-secondary" onClick={() => { setError(""); setPwModal(true); }}>{t("changePassword")}</button>
          {me?.role==="admin" && <button className="btn btn-primary" onClick={() => { setError(""); setModal(true); }}>{t("inviteUser")}</button>}
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <thead><tr><th>{t("name")}</th><th>{t("email")}</th><th>{t("role")}</th><th>{t("active")}</th><th>{t("joined")}</th><th></th></tr></thead>
          <tbody>
            {users.map(u => (
              <tr key={u.id}>
                <td><strong>{u.name}</strong>{u.id===me.id && <span style={{ fontSize:11,color:"var(--gold)",marginLeft:8 }}>YOU</span>}</td>
                <td className="text-dim">{u.email}</td>
                <td><span className="badge badge-draft">{u.role==="admin"?t("adminRole"):t("userRole")}</span></td>
                <td><span className={`badge badge-${u.active?"paid":"cancelled"}`}>{u.active?t("active"):t("deactivate")}</span></td>
                <td className="text-dim">{new Date(u.created_at).toLocaleDateString()}</td>
                <td style={{ textAlign:"right" }}>
                  {(me?.role==="admin" || u.id===me.id) && (
                    <button className="btn btn-ghost btn-sm" onClick={() => { setEditForm({name:u.name,email:u.email,role:u.role,active:u.active}); setEditModal(u); }}>{t("edit")}</button>
                  )}
                  {me?.role==="admin" && u.id!==me.id && (
                    <button className="btn btn-ghost btn-sm" style={{ marginLeft:6 }} onClick={() => toggleActive(u)}>
                      {u.active?t("deactivate"):t("activate")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Invite modal */}
      {modal && (
        <div className="modal-overlay" onClick={() => setModal(false)}>
          <div className="modal" onClick={e=>e.stopPropagation()}>
            <div className="modal-header"><h3>{t("inviteUser").toUpperCase()}</h3><button className="btn btn-ghost btn-icon" onClick={() => setModal(false)}>✕</button></div>
            <div className="modal-body">
              <p style={{ color:"var(--text-dim)",fontSize:13,marginBottom:16 }}>{t("inviteEmailNote")}</p>
              {error && <div className="login-error">{error}</div>}
              <div className="form-grid">
                <div className="form-group"><label>{t("name")} *</label><input value={form.name} onChange={f("name")} required autoFocus /></div>
                <div className="form-group"><label>{t("email")} *</label><input type="email" value={form.email} onChange={f("email")} required /></div>
                <div className="form-group full"><label>{t("role")}</label>
                  <select value={form.role} onChange={f("role")}><option value="user">{t("userRole")}</option><option value="admin">{t("adminRole")}</option></select>
                </div>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setModal(false)}>{t("cancel")}</button>
              <button className="btn btn-primary" onClick={invite} disabled={saving}>{saving?t("saving"):t("inviteUser")}</button>
            </div>
          </div>
        </div>
      )}

      {/* Edit modal */}
      {editModal && (
        <div className="modal-overlay" onClick={() => setEditModal(null)}>
          <div className="modal" onClick={e=>e.stopPropagation()}>
            <div className="modal-header"><h3>{t("edit").toUpperCase()}</h3><button className="btn btn-ghost btn-icon" onClick={() => setEditModal(null)}>✕</button></div>
            <div className="modal-body">
              {error && <div className="login-error">{error}</div>}
              <div className="form-grid">
                <div className="form-group"><label>{t("name")}</label><input value={editForm.name} onChange={ef("name")} /></div>
                <div className="form-group"><label>{t("email")}</label><input type="email" value={editForm.email} onChange={ef("email")} /></div>
                {me?.role==="admin" && (
                  <div className="form-group full"><label>{t("role")}</label>
                    <select value={editForm.role} onChange={ef("role")}><option value="user">{t("userRole")}</option><option value="admin">{t("adminRole")}</option></select>
                  </div>
                )}
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setEditModal(null)}>{t("cancel")}</button>
              <button className="btn btn-primary" onClick={saveEdit} disabled={saving}>{saving?t("saving"):t("save")}</button>
            </div>
          </div>
        </div>
      )}

      {/* Change password modal */}
      {pwModal && (
        <div className="modal-overlay" onClick={() => setPwModal(false)}>
          <div className="modal" onClick={e=>e.stopPropagation()}>
            <div className="modal-header"><h3>{t("changePassword").toUpperCase()}</h3><button className="btn btn-ghost btn-icon" onClick={() => setPwModal(false)}>✕</button></div>
            <div className="modal-body">
              {error && <div className="login-error">{error}</div>}
              <div className="form-group" style={{ marginBottom:12 }}><label>{t("currentPassword")}</label><input type="password" value={pwForm.currentPassword} onChange={pf("currentPassword")} autoFocus /></div>
              <div className="form-group" style={{ marginBottom:12 }}><label>{t("newPassword")}</label><input type="password" value={pwForm.newPassword} onChange={pf("newPassword")} /></div>
              <div className="form-group"><label>{t("confirmPassword")}</label><input type="password" value={pwForm.confirmPassword} onChange={pf("confirmPassword")} /></div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setPwModal(false)}>{t("cancel")}</button>
              <button className="btn btn-primary" onClick={changePassword} disabled={saving}>{saving?t("saving"):t("save")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
