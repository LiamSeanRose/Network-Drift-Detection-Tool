// i18n.js — minimal, dependency-free EN/FR internationalization scaffold.
//
// Government of Canada tools must be bilingual (Official Languages Act). This is
// the scaffold, not the finished translation: the infrastructure (a language
// toggle that flips <html lang>, a persisted choice, a t() lookup with English
// fallback) plus reviewed Canadian-French for the critical-path strings. New
// strings are added by id to TRANSLATIONS; an untranslated id falls back to
// English so a partial catalog never renders blank.

import { createContext, useCallback, useContext, useEffect, useState } from 'react'

const STORAGE_KEY = 'netdrift_lang'
export const LANGS = ['en', 'fr']

// English is the source of truth; French is reviewed Canadian-French.
export const TRANSLATIONS = {
  en: {
    'lang.name': 'English',
    'app.refresh': 'Refresh',
    'app.refreshing': 'Refreshing…',
    'app.help': 'Help',
    'app.driftConsole': 'drift console',
    'section.configuration': 'configuration',
    'section.driftEvents': 'drift events',
    'state.loading': 'Loading…',
    'state.noDrift': 'No drift events yet.',
    'accuracy.caption': 'inventory verified accurate',
    'fleet.title': 'Fleet health',
    'fleet.lastActivity': 'Last activity:',
    'fleet.noDrift': 'No drift detected yet.',
    'fleet.loading': 'Loading fleet health…',
    'fleet.error': 'Failed to load fleet health:',
  },
  fr: {
    'lang.name': 'Français',
    'app.refresh': 'Actualiser',
    'app.refreshing': 'Actualisation…',
    'app.help': 'Aide',
    'app.driftConsole': 'console de dérive',
    'section.configuration': 'configuration',
    'section.driftEvents': 'événements de dérive',
    'state.loading': 'Chargement…',
    'state.noDrift': "Aucun événement de dérive pour l'instant.",
    'accuracy.caption': 'inventaire confirmé exact',
    'fleet.title': 'État du parc',
    'fleet.lastActivity': 'Dernière activité :',
    'fleet.noDrift': "Aucune dérive détectée pour l'instant.",
    'fleet.loading': "Chargement de l'état du parc…",
    'fleet.error': "Échec du chargement de l'état du parc :",
  },
}

const I18nContext = createContext(null)

// Standalone translate, used by the hook and by the no-provider fallback.
function translate(lang, key) {
  const table = TRANSLATIONS[lang] || TRANSLATIONS.en
  return table[key] ?? TRANSLATIONS.en[key] ?? key
}

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY)
    return LANGS.includes(saved) ? saved : 'en'
  })

  // Keep the document language attribute in sync — assistive tech and the
  // browser's own UI rely on <html lang> being correct.
  useEffect(() => { document.documentElement.lang = lang }, [lang])

  const setLang = useCallback((next) => {
    if (!LANGS.includes(next)) return
    setLangState(next)
    localStorage.setItem(STORAGE_KEY, next)
  }, [])

  const t = useCallback((key) => translate(lang, key), [lang])

  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
      {children}
    </I18nContext.Provider>
  )
}

// useI18n works with or without a provider: outside one (e.g. a component-level
// unit test that renders the component directly) it returns an English
// passthrough, so existing tests need no provider wrapper.
export function useI18n() {
  const ctx = useContext(I18nContext)
  if (ctx) return ctx
  return { lang: 'en', setLang: () => {}, t: (key) => translate('en', key) }
}

// LanguageToggle — the EN / FR switch. Lives in each view's header.
export function LanguageToggle() {
  const { lang, setLang } = useI18n()
  return (
    <div className="lang-toggle" role="group" aria-label="Language / Langue">
      {LANGS.map((code) => (
        <button
          key={code}
          type="button"
          className={`lang-toggle__btn${lang === code ? ' is-active' : ''}`}
          aria-pressed={lang === code}
          onClick={() => setLang(code)}
        >
          {code.toUpperCase()}
        </button>
      ))}
    </div>
  )
}
