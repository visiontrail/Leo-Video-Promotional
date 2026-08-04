import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  fetchAccountAutomations,
  fetchAccountRuns,
  runAccountAutomation,
  updateAccountAutomation,
} from '../api'
import type { AccountAutomation, AccountAutomationUpdate, AccountRun } from '../api'

const ACTIVE = new Set(['queued', 'planning', 'generating_image', 'publishing'])

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

function runTitle(run: AccountRun): string {
  return run.title || `Today in History · ${run.event_date}`
}

function automationDraft(automation: AccountAutomation): AccountAutomationUpdate {
  return {
    name: automation.name,
    account_handle: automation.account_handle,
    enabled: automation.enabled,
    schedule_time: automation.schedule_time,
    timezone: automation.timezone,
    prompt_template: automation.prompt_template,
    executor: automation.executor,
    opencode_model: automation.opencode_model,
  }
}

function AutomationEditor({ automation }: { automation: AccountAutomation }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<AccountAutomationUpdate>(() => automationDraft(automation))

  const save = useMutation({
    mutationFn: () => updateAccountAutomation(automation.id, draft),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['account-automations'] }),
  })
  const runNow = useMutation({
    mutationFn: () => runAccountAutomation(automation.id),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ['account-runs'] })
      navigate(`/account-operations/runs/${run.id}`)
    },
  })

  const set = <K extends keyof AccountAutomationUpdate>(key: K, value: AccountAutomationUpdate[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  return (
    <section className="ops-config-panel">
      <div className="ops-config-head">
        <div>
          <span className="eyebrow">Daily commission</span>
          <h2>Today in History</h2>
          <p>A quiet historical record, researched and illustrated through ChatGPT Web.</p>
        </div>
        <label className="ops-switch">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => set('enabled', event.target.checked)}
          />
          <span>{draft.enabled ? 'Scheduled' : 'Paused'}</span>
        </label>
      </div>

      <div className="ops-route-line" aria-label="Operation route">
        <span>{draft.executor === 'opencode' ? 'OpenCode' : 'Pipeline'}</span><i>→</i><span>OpenCLI</span><i>→</i><span>ChatGPT</span><i>→</i><span>@{draft.account_handle}</span>
      </div>

      <div className="ops-form-grid">
        <div className="form-group">
          <label htmlFor="ops-account">X account</label>
          <input id="ops-account" value={draft.account_handle} onChange={(event) => set('account_handle', event.target.value)} />
        </div>
        <div className="form-group">
          <label htmlFor="ops-time">Daily time</label>
          <input id="ops-time" type="time" value={draft.schedule_time} onChange={(event) => set('schedule_time', event.target.value)} />
        </div>
        <div className="form-group">
          <label htmlFor="ops-timezone">Timezone</label>
          <input id="ops-timezone" value={draft.timezone} onChange={(event) => set('timezone', event.target.value)} />
        </div>
        <div className="form-group">
          <label htmlFor="ops-executor">Orchestrator</label>
          <select id="ops-executor" value={draft.executor} onChange={(event) => set('executor', event.target.value as AccountAutomationUpdate['executor'])}>
            <option value="opencode">OpenCode agent</option>
            <option value="pipeline">Deterministic pipeline</option>
          </select>
        </div>
        {draft.executor === 'opencode' && (
          <div className="form-group ops-model-field">
            <label htmlFor="ops-model">OpenCode model</label>
            <input id="ops-model" value={draft.opencode_model} onChange={(event) => set('opencode_model', event.target.value)} />
          </div>
        )}
        <div className="form-group ops-prompt-field">
          <label htmlFor="ops-prompt">Curatorial prompt</label>
          <textarea id="ops-prompt" value={draft.prompt_template} onChange={(event) => set('prompt_template', event.target.value)} spellCheck={false} />
          <small>Available variables: {'{date}'}, {'{month_name}'}, {'{day}'}, {'{year}'}</small>
        </div>
      </div>

      <div className="ops-config-foot">
        <p>Next edition <strong>{draft.enabled ? formatDateTime(automation.next_run_at) : 'paused'}</strong></p>
        <div>
          <button type="button" className="btn-ghost" disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : 'Save commission'}
          </button>
          <button type="button" className="btn-primary" disabled={runNow.isPending} onClick={() => runNow.mutate()}>
            {runNow.isPending ? 'Queuing…' : 'Run now'}
          </button>
        </div>
      </div>
      {(save.isError || runNow.isError) && (
        <div className="error-box">{((save.error || runNow.error) as Error).message}</div>
      )}
    </section>
  )
}

export default function AccountOperations() {
  const navigate = useNavigate()
  const { data: automations, isLoading: loadingAutomations } = useQuery({
    queryKey: ['account-automations'],
    queryFn: fetchAccountAutomations,
  })
  const { data: runs, isLoading: loadingRuns } = useQuery({
    queryKey: ['account-runs'],
    queryFn: fetchAccountRuns,
    refetchInterval: (query) => query.state.data?.some((run) => ACTIVE.has(run.status)) ? 2500 : false,
  })
  const published = runs?.filter((run) => run.status === 'published').length ?? 0
  const active = runs?.filter((run) => ACTIVE.has(run.status)).length ?? 0
  const failed = runs?.filter((run) => run.status === 'failed').length ?? 0

  return (
    <section className="ops-workspace">
      <header className="ops-command">
        <div className="ops-title-block">
          <span className="eyebrow">Quiet Atlas desk</span>
          <h1>Account<br />Operations</h1>
          <p>Autonomous editions with a complete publishing record.</p>
        </div>
        <dl className="ops-totals">
          <div><dt>Live desks</dt><dd>{automations?.filter((item) => item.enabled).length ?? 0}</dd></div>
          <div><dt>In flight</dt><dd>{active}</dd></div>
          <div><dt>Published</dt><dd>{published}</dd></div>
          <div><dt>Failed</dt><dd>{failed}</dd></div>
        </dl>
      </header>

      <div className="ops-layout">
        {loadingAutomations ? <div className="table-message">Loading commission…</div> : automations?.[0] ? (
          <AutomationEditor key={automations[0].updated_at} automation={automations[0]} />
        ) : (
          <div className="empty-state">No account automation configured.</div>
        )}

        <section className="ops-ledger">
          <div className="ops-ledger-head">
            <div><span className="eyebrow">Publication ledger</span><h2>Every edition</h2></div>
            <span>{runs?.length ?? 0} records</span>
          </div>
          {loadingRuns ? (
            <div className="table-message">Loading publication ledger…</div>
          ) : !runs?.length ? (
            <div className="ops-ledger-empty"><strong>The atlas is blank.</strong><span>Run the commission to publish its first dated entry.</span></div>
          ) : (
            <div className="ops-table-scroll">
              <table className="ops-table">
                <thead><tr><th>Edition</th><th>Status</th><th>Trigger</th><th>Account</th><th>Created</th><th /></tr></thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.id} tabIndex={0} onClick={() => navigate(`/account-operations/runs/${run.id}`)} onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') navigate(`/account-operations/runs/${run.id}`)
                    }}>
                      <td><strong>{runTitle(run)}</strong><small>{run.event_date} · {run.id.slice(-6)}</small></td>
                      <td><span className={`badge ${run.status}`}>{run.status.replace('_', ' ')}</span></td>
                      <td>{run.trigger}</td><td>@{run.account_handle}</td><td>{formatDateTime(run.created_at)}</td><td><span className="row-arrow">→</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </section>
  )
}
