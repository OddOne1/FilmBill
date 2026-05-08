import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import { api } from "../api";
import { useI18n } from "./I18nContext";

const SettingsCtx = createContext(null);

export function SettingsProvider({ children }) {
  const [settings, setSettings] = useState({
    currency_symbol:"€", currency_code:"EUR", currency_locale:"de-AT",
    company_name:"FilmBill", language:"de",
    primary_color:"#f5c842", secondary_color:"#4dabf7",
    bg_color:"#0d0d0d", surface_color:"#161616",
    app_font:"DM Sans", doc_font:"DM Sans", custom_font_url:"",
  });
  const { setLang } = useI18n();

  // Calculate luminance of a hex color (0 = black, 1 = white)
  function luminance(hex) {
    if (!hex) return 0;
    const c = hex.replace('#','');
    const r = parseInt(c.substring(0,2), 16) / 255;
    const g = parseInt(c.substring(2,4), 16) / 255;
    const b = parseInt(c.substring(4,6), 16) / 255;
    return 0.299*r + 0.587*g + 0.114*b;
  }

  // Apply theme to CSS variables + load custom font
  useEffect(() => {
    const root = document.documentElement;
    if (settings.primary_color)   root.style.setProperty('--gold',     settings.primary_color);
    if (settings.secondary_color) root.style.setProperty('--blue',     settings.secondary_color);
    if (settings.bg_color)        root.style.setProperty('--bg',       settings.bg_color);
    if (settings.surface_color)   root.style.setProperty('--surface',  settings.surface_color);
    if (settings.app_font)        root.style.setProperty('--sans',     `"${settings.app_font}", sans-serif`);
    if (settings.doc_font)        root.style.setProperty('--doc-font', `"${settings.doc_font}", sans-serif`);

    // Auto-adjust text colors based on background brightness
    if (settings.bg_color) {
      const lum = luminance(settings.bg_color);
      const isLight = lum > 0.5;
      if (isLight) {
        root.style.setProperty('--text',     '#1a1a1a');
        root.style.setProperty('--text-dim', '#666');
        root.style.setProperty('--border',   'rgba(0,0,0,0.15)');
        root.style.setProperty('--surface2', 'rgba(0,0,0,0.05)');
      } else {
        root.style.setProperty('--text',     '#e8e8e8');
        root.style.setProperty('--text-dim', '#888');
        root.style.setProperty('--border',   '#2a2a2a');
        root.style.setProperty('--surface2', '#1e1e1e');
      }
    }

    // Load custom font from URL (e.g. Google Fonts)
    const fontId = "filmbill-custom-font";
    let link = document.getElementById(fontId);
    if (settings.custom_font_url) {
      if (!link) {
        link = document.createElement("link");
        link.id = fontId; link.rel = "stylesheet";
        document.head.appendChild(link);
      }
      link.href = settings.custom_font_url;
    } else if (link) {
      link.remove();
    }
  }, [settings]);

  const load = useCallback(() => {
    api.get("/settings").then(s => {
      setSettings(s);
      if (s.language) setLang(s.language);
    }).catch(console.error);
  }, [setLang]);

  useEffect(() => { load(); }, [load]);

  function fmt(n) {
    try {
      return Number(n||0).toLocaleString(settings.currency_locale||"de-AT", {
        style:"currency", currency:settings.currency_code||"EUR",
      });
    } catch {
      return (settings.currency_symbol||"€") + Number(n||0).toFixed(2);
    }
  }

  return (
    <SettingsCtx.Provider value={{ settings, setSettings, reload:load, fmt }}>
      {children}
    </SettingsCtx.Provider>
  );
}

export const useSettings = () => useContext(SettingsCtx);
