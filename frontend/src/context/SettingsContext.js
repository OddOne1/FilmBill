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
  });
  const { setLang } = useI18n();

  // Apply colors to CSS variables
  useEffect(() => {
    const root = document.documentElement;
    if (settings.primary_color)   root.style.setProperty('--gold',     settings.primary_color);
    if (settings.secondary_color) root.style.setProperty('--blue',     settings.secondary_color);
    if (settings.bg_color)        root.style.setProperty('--bg',       settings.bg_color);
    if (settings.surface_color)   root.style.setProperty('--surface',  settings.surface_color);
  }, [settings.primary_color, settings.secondary_color, settings.bg_color, settings.surface_color]);

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
