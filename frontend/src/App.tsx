import { Routes, Route, Link } from 'react-router-dom'
import TaskList from './components/TaskList'
import TaskForm from './components/TaskForm'
import TaskDetail from './components/TaskDetail'
import SettingsPage from './components/SettingsPage'

export default function App() {
  return (
    <>
      <div className="header">
        <Link to="/" style={{ textDecoration: 'none' }}>
          <h1>Video Promotional</h1>
        </Link>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Link to="/settings">
            <button>Settings</button>
          </Link>
          <Link to="/new">
            <button className="btn-primary">+ New Task</button>
          </Link>
        </div>
      </div>
      <Routes>
        <Route path="/" element={<TaskList />} />
        <Route path="/new" element={<TaskForm />} />
        <Route path="/tasks/:id" element={<TaskDetail />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </>
  )
}
