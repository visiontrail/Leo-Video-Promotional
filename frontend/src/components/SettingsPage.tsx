import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchProviders,
  createProvider,
  updateProvider,
  deleteProvider,
} from '../api'
import type { Provider, ProviderInput } from '../api'

const EMPTY_FORM: ProviderInput = {
  name: '',
  endpoint: '',
  api_key: '',
  model: '',
  is_default: false,
}

export default function SettingsPage() {
  const queryClient = useQueryClient()
  const { data: providers = [], isLoading } = useQuery({
    queryKey: ['providers'],
    queryFn: fetchProviders,
  })

  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<ProviderInput>(EMPTY_FORM)
  const [showForm, setShowForm] = useState(false)

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['providers'] })

  const saveMutation = useMutation({
    mutationFn: () =>
      editingId === null
        ? createProvider(form)
        : updateProvider(editingId, form),
    onSuccess: () => {
      invalidate()
      resetForm()
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteProvider(id),
    onSuccess: invalidate,
  })

  const defaultMutation = useMutation({
    mutationFn: (id: number) => updateProvider(id, { is_default: true }),
    onSuccess: invalidate,
  })

  function resetForm() {
    setForm(EMPTY_FORM)
    setEditingId(null)
    setShowForm(false)
  }

  function startEdit(p: Provider) {
    setEditingId(p.id)
    setForm({ name: p.name, endpoint: p.endpoint, api_key: '', model: p.model, is_default: p.is_default })
    setShowForm(true)
  }

  const canSave = form.name.trim() && form.endpoint.trim() && form.model.trim()

  return (
    <div className="card" style={{ maxWidth: 800, margin: '0 auto' }}>
      <h2 style={{ marginBottom: 20, fontSize: 18, fontWeight: 600 }}>AI Providers</h2>

      {isLoading ? (
        <p>Loading…</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 20 }}>
          {providers.length === 0 && <p style={{ color: 'var(--text-dim)' }}>No providers configured.</p>}
          {providers.map((p) => (
            <div
              key={p.id}
              className="card"
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 12 }}
            >
              <div>
                <div style={{ fontWeight: 600 }}>
                  {p.name} {p.is_default && <span style={{ fontSize: 12, color: 'var(--accent, #4f9)' }}>• default</span>}
                </div>
                <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
                  {p.model} — {p.endpoint}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>key: {p.api_key_masked || '(none)'}</div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                {!p.is_default && (
                  <button onClick={() => defaultMutation.mutate(p.id)}>Set default</button>
                )}
                <button onClick={() => startEdit(p)}>Edit</button>
                <button onClick={() => deleteMutation.mutate(p.id)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showForm ? (
        <div className="card" style={{ padding: 16 }}>
          <h3 style={{ marginBottom: 12, fontSize: 15 }}>{editingId === null ? 'Add Provider' : 'Edit Provider'}</h3>
          <div className="form-group">
            <label>Name</label>
            <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Local Ollama" />
          </div>
          <div className="form-group">
            <label>Endpoint</label>
            <input
              value={form.endpoint}
              onChange={(e) => setForm({ ...form, endpoint: e.target.value })}
              placeholder="http://localhost:11434/v1/chat/completions"
            />
          </div>
          <div className="grid-2">
            <div className="form-group">
              <label>Model</label>
              <input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="llama3" />
            </div>
            <div className="form-group">
              <label>API Key {editingId !== null && '(leave blank to keep)'}</label>
              <input
                type="password"
                value={form.api_key ?? ''}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder="sk-…"
              />
            </div>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 14, marginBottom: 12 }}>
            <input
              type="checkbox"
              checked={form.is_default ?? false}
              onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
              style={{ width: 'auto' }}
            />
            Set as default provider
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn-primary" disabled={!canSave || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
              {saveMutation.isPending ? 'Saving…' : 'Save'}
            </button>
            <button onClick={resetForm}>Cancel</button>
          </div>
          {saveMutation.isError && (
            <div className="error-box" style={{ marginTop: 12 }}>{(saveMutation.error as Error).message}</div>
          )}
        </div>
      ) : (
        <button className="btn-primary" onClick={() => { setForm(EMPTY_FORM); setEditingId(null); setShowForm(true) }}>
          + Add Provider
        </button>
      )}
    </div>
  )
}
