import { useState } from 'react'
import ProvidersPanel from './ProvidersPanel'
import PromptsPanel from './PromptsPanel'
import SkillsPanel from './SkillsPanel'

type Tab = 'models' | 'prompts' | 'skills'

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'models', label: 'Models', hint: 'AI providers & endpoints' },
  { id: 'prompts', label: 'Prompts', hint: 'Pipeline system prompts' },
  { id: 'skills', label: 'Skills', hint: 'HyperFrames skill bundles' },
]

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>('models')

  return (
    <div className="admin-console">
      <header className="admin-header">
        <h1 className="admin-title">Admin Console</h1>
        <p className="admin-subtitle">Manage the models, prompts, and skills that drive the pipeline.</p>
      </header>

      <nav className="admin-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`admin-tab ${tab === t.id ? 'is-active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            <span className="admin-tab-label">{t.label}</span>
            <span className="admin-tab-hint">{t.hint}</span>
          </button>
        ))}
      </nav>

      <section className="card admin-panel">
        {tab === 'models' && <ProvidersPanel />}
        {tab === 'prompts' && <PromptsPanel />}
        {tab === 'skills' && <SkillsPanel />}
      </section>
    </div>
  )
}
