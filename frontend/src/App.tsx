import { useEffect, useState } from 'react'
import { Routes, Route, Link } from 'react-router-dom'
import TaskList from './components/TaskList'
import TaskForm from './components/TaskForm'
import TaskDetail from './components/TaskDetail'
import SettingsPage from './components/SettingsPage'

type Theme = 'light' | 'dark'

export default function App() {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = localStorage.getItem('theme')
    if (stored === 'light' || stored === 'dark') return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('theme', theme)
  }, [theme])

  const nextTheme = theme === 'dark' ? 'light' : 'dark'

  return (
    <div className="app-shell">
      <div className="ambient-field" aria-hidden="true" />
      <div className="header">
        <Link to="/" className="brand-lockup">
          <span className="brand-mark">VP</span>
          <span>
            <h1>Video Promotional</h1>
            <small>AI podcast video pipeline</small>
          </span>
        </Link>
        <div className="header-actions">
          <button
            className="theme-toggle"
            type="button"
            aria-label={`Switch to ${nextTheme} theme`}
            onClick={() => setTheme(nextTheme)}
          >
            <span>{theme === 'dark' ? 'Light' : 'Dark'}</span>
          </button>
          <Link to="/settings">
            <button className="btn-ghost">Settings</button>
          </Link>
          <Link to="/new">
            <button className="btn-primary">New Task</button>
          </Link>
        </div>
      </div>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<TaskList />} />
          <Route path="/new" element={<TaskForm />} />
          <Route path="/tasks/:id" element={<TaskDetail />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  )
}
