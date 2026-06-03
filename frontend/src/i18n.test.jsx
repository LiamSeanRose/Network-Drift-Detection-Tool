// Tests for the EN/FR i18n scaffold.

import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { I18nProvider, LanguageToggle, useI18n, TRANSLATIONS, LANGS } from './i18n'

function Probe() {
  const { t, lang } = useI18n()
  return (
    <div>
      <span data-testid="cap">{t('accuracy.caption')}</span>
      <span data-testid="lang">{lang}</span>
    </div>
  )
}

describe('i18n', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.lang = 'en'
  })

  it('exposes exactly en and fr', () => {
    expect(LANGS).toEqual(['en', 'fr'])
  })

  it('has a French translation for every English key (and no orphans)', () => {
    for (const key of Object.keys(TRANSLATIONS.en)) {
      expect(TRANSLATIONS.fr[key], `missing fr translation for "${key}"`).toBeTruthy()
    }
    for (const key of Object.keys(TRANSLATIONS.fr)) {
      expect(TRANSLATIONS.en[key], `orphan fr key "${key}"`).toBeTruthy()
    }
  })

  it('falls back to English passthrough with no provider', () => {
    render(<Probe />)
    expect(screen.getByTestId('cap')).toHaveTextContent('inventory verified accurate')
    expect(screen.getByTestId('lang')).toHaveTextContent('en')
  })

  it('switches to French when toggled, and sets <html lang>', () => {
    render(<I18nProvider><LanguageToggle /><Probe /></I18nProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'FR' }))
    expect(screen.getByTestId('cap')).toHaveTextContent('inventaire confirmé exact')
    expect(screen.getByTestId('lang')).toHaveTextContent('fr')
    expect(document.documentElement.lang).toBe('fr')
  })

  it('persists the language choice', () => {
    render(<I18nProvider><LanguageToggle /></I18nProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'FR' }))
    expect(localStorage.getItem('netdrift_lang')).toBe('fr')
  })

  it('marks the active language button as pressed', () => {
    render(<I18nProvider><LanguageToggle /></I18nProvider>)
    expect(screen.getByRole('button', { name: 'EN' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'FR' }))
    expect(screen.getByRole('button', { name: 'FR' })).toHaveAttribute('aria-pressed', 'true')
  })
})
