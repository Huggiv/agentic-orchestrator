import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import FlowDiagram, { StageIcon } from './FlowDiagram'

const FLOW_STEPS = [
  { key: 'clone_repository', label: 'Clone Repo' },
  { key: 'auth_setup', label: 'Auth Setup' },
  { key: 'prepare_branch', label: 'Prepare Branch' },
  { key: 'read_jira', label: 'Read Jira' },
  { key: 'agentic_implementation', label: 'Agentic Impl' },
  { key: 'commit_changes', label: 'Commit Changes' },
  { key: 'push_branch', label: 'Push Branch' },
  { key: 'create_pr', label: 'Create PR' },
]

const defaultPlan = [
  'Analyze impacted files',
  'Apply code changes in small commits',
  'Run tests and lint checks',
]

const JOB_STATUS_LABELS = {
  idle: 'Ready',
  queued: 'Queued',
  running: 'Executing',
  success: 'Completed',
  failed: 'Failed',
}

const STEP_ALIASES = {
  create_and_checkout_branch: 'prepare_branch',
  read_jira_issue: 'read_jira',
  copilot_agentic_plan: 'agentic_implementation',
}

const STEP_STATUS_LABELS = {
  success: 'Done',
  skipped: 'Skipped',
  failed: 'Failed',
  running: 'Running',
  queued: 'Queued',
  idle: 'Idle',
}

const normalizeStepKey = (value) => STEP_ALIASES[value] || value

const collectHistoryStepStatus = (entry) => {
  const statusMap = {}
  ;(entry.progress || []).forEach((item) => {
    statusMap[normalizeStepKey(item.name)] = item.status || 'idle'
  })
  ;(entry.result?.steps || []).forEach((item) => {
    statusMap[normalizeStepKey(item.name)] = item.status || statusMap[normalizeStepKey(item.name)] || 'idle'
  })
  return statusMap
}

const computeFlowProgress = (entry) => {
  const statuses = collectHistoryStepStatus(entry)
  const done = FLOW_STEPS.filter((step) => {
    const status = statuses[step.key]
    return status === 'success' || status === 'skipped'
  }).length
  return { done, total: FLOW_STEPS.length }
}

function HistoryStepDiagram({ entry }) {
  const statuses = collectHistoryStepStatus(entry)

  return (
    <div className="history-step-diagram" aria-label="Job flow steps">
      {FLOW_STEPS.map((step) => {
        const status = statuses[step.key] || 'idle'
        return (
          <div key={`${entry.id}-${step.key}`} className={`history-step history-step-${status}`}>
            <svg width="26" height="26" viewBox="0 0 28 28" role="img" aria-label={step.label}>
              <circle cx="14" cy="14" r="12" className="history-step-ring" />
              <g transform="translate(0,0)">
                <StageIcon stepKey={step.key} color="currentColor" />
              </g>
            </svg>
            <span className="history-step-label">{step.label}</span>
            <span className="history-step-status">{STEP_STATUS_LABELS[status] || status}</span>
          </div>
        )
      })}
    </div>
  )
}

const parseApiPayload = (raw) => {
  if (!raw) return {}
  try {
    return JSON.parse(raw)
  } catch {
    return { detail: raw }
  }
}

const formatInt = (value) => {
  if (value === null || value === undefined) return '-'
  return new Intl.NumberFormat().format(Number(value))
}

const formatCredits = (value) => {
  if (value === null || value === undefined) return '-'
  return Number(value).toFixed(2)
}

const formatCost = (value) => {
  if (value === null || value === undefined) return '-'
  return Number(value).toFixed(4)
}

const CollapsiblePanel = ({
  id,
  title,
  children,
  defaultExpanded = false,
  expandedPanels,
  togglePanel,
}) => {
  const isExpanded = expandedPanels[id] !== undefined ? expandedPanels[id] : defaultExpanded
  return (
    <div className="collapsible-panel top-collapsible-panel">
      <button className="panel-header" onClick={() => togglePanel(id)}>
        <span className="chevron">{isExpanded ? '▼' : '▶'}</span>
        <h3>{title}</h3>
      </button>
      {isExpanded && <div className="panel-content">{children}</div>}
    </div>
  )
}

export default function App() {
  const [ticket, setTicket] = useState('')
  const [repository, setRepository] = useState('vittal-huggi_ADVNTST/v93000_telemetry_station')
  const [reviewer, setReviewer] = useState('')
  const [result, setResult] = useState(null)
  const [jobStatus, setJobStatus] = useState('idle')
  const [progress, setProgress] = useState([])
  const [error, setError] = useState('')
  const [history, setHistory] = useState([])
  const [activeTab, setActiveTab] = useState('run')
  const [expandedPanels, setExpandedPanels] = useState({ trigger: true })
  const [selectedArtifact, setSelectedArtifact] = useState(null)

  const loadHistory = async () => {
    const response = await fetch('/api/orchestrate/history?limit=30&include_progress=true')
    const raw = await response.text()
    const data = parseApiPayload(raw)
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to fetch orchestration history')
    }
    setHistory(Array.isArray(data.items) ? data.items : [])
  }

  useEffect(() => {
    loadHistory().catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    const hasRunningJobs = history.some((entry) => entry.status === 'queued' || entry.status === 'running')
    if (!hasRunningJobs) return undefined

    const timer = setInterval(() => {
      loadHistory().catch(() => undefined)
    }, 3000)

    return () => clearInterval(timer)
  }, [history])

  const togglePanel = (panelId) => {
    setExpandedPanels((prev) => ({
      ...prev,
      [panelId]: !prev[panelId],
    }))
  }

  const triggerOrchestration = async (event) => {
    event.preventDefault()
    setError('')
    setResult(null)
    setProgress([])
    setJobStatus('queued')

    try {
      const response = await fetch('/api/orchestrate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jira_ticket_id: ticket,
          repository,
          base_branch: 'development',
          reviewer: reviewer || null,
          commit_message: `feat(${ticket.toLowerCase()}): automated implementation`,
          change_plan: defaultPlan,
        }),
      })

      const raw = await response.text()
      const data = parseApiPayload(raw)
      if (!response.ok) throw new Error(data.detail || 'Orchestration failed')

      const jobId = data.job_id
      if (!jobId) throw new Error('Orchestration job id missing from backend response')

      for (let i = 0; i < 180; i += 1) {
        const statusResponse = await fetch(`/api/orchestrate/${jobId}`)
        const statusRaw = await statusResponse.text()
        const statusData = parseApiPayload(statusRaw)
        if (!statusResponse.ok) throw new Error(statusData.detail || 'Failed to get orchestration status')

        setJobStatus(statusData.status)
        setProgress(statusData.progress || [])

        if (statusData.status === 'success') {
          setResult(statusData.result)
          await loadHistory()
          return
        }

        if (statusData.status === 'failed') {
          await loadHistory()
          throw new Error(statusData.error || 'Orchestration failed')
        }

        await new Promise((resolve) => setTimeout(resolve, 1200))
      }

      throw new Error('Timed out waiting for orchestration completion')
    } catch (err) {
      setError(err.message)
      setJobStatus('failed')
    }
  }

  const isRunning = jobStatus === 'queued' || jobStatus === 'running'
  const showFlow = isRunning || progress.length > 0 || jobStatus === 'success' || jobStatus === 'failed'
  const completedSteps = progress.filter((event) => event.status === 'success' || event.status === 'skipped').length
  const latestProgress = progress.length > 0 ? progress[progress.length - 1] : null
  const copilotAuthSource = progress
    .filter((event) => event.name === 'auth_setup' && event.status === 'success')
    .at(-1)?.details

  const buildJiraLink = (entry) => {
    const ticketId = entry.request?.jira_ticket_id
    const jiraBaseUrl = entry.request?.jira_url
    if (!ticketId || !jiraBaseUrl) return null
    return `${jiraBaseUrl.replace(/\/$/, '')}/browse/${ticketId}`
  }

  const renderUsageSummary = (usage) => {
    if (!usage) return null

    const changes = usage.changes || {}
    const tokens = usage.tokens || {}
    const ai = usage.ai || {}

    return (
      <div className="usage-summary">
        <div className="floating-header-shell">
          <header className="hero">
            <div className="hero-copy">
              <span className="hero-eyebrow">Autonomous Delivery Control Plane</span>
              <h1>Agentic Orchestration Console</h1>
              <p>Drive Jira-scoped implementation, validation, and pull request creation through a Copilot-powered execution path.</p>
            </div>
            <div className="hero-metrics">
              <div className="hero-metric">
                <span className="hero-metric-value">{JOB_STATUS_LABELS[jobStatus] || jobStatus}</span>
                <span className="hero-metric-label">Current Run</span>
              </div>
              <div className="hero-metric">
                <span className="hero-metric-value">{history.length}</span>
                <span className="hero-metric-label">Stored Runs</span>
              </div>
            </div>
          </header>

          <nav className="tabs-navigation">
            <button className={`tab-button${activeTab === 'run' ? ' active' : ''}`} onClick={() => setActiveTab('run')}>
              Run
            </button>
            <button className={`tab-button${activeTab === 'history' ? ' active' : ''}`} onClick={() => setActiveTab('history')}>
              History {history.length > 0 && <span className="tab-badge">{history.length}</span>}
            </button>
          </nav>
        </div>
        <div className="hero-metrics">
          <div className="hero-metric">
            <span className="hero-metric-value">{JOB_STATUS_LABELS[jobStatus] || jobStatus}</span>
            <span className="hero-metric-label">Current Run</span>
          </div>
          <div className="hero-metric">
            <span className="hero-metric-value">{history.length}</span>
            <span className="hero-metric-label">Stored Runs</span>
          </div>
          <div className="hero-metric">
            <span className="hero-metric-value">{completedSteps}/{FLOW_STEPS.length}</span>
            <span className="hero-metric-label">Flow Progress</span>
          </div>
        </div>
      </header>

      <nav className="tabs-navigation">
        <button className={`tab-button${activeTab === 'run' ? ' active' : ''}`} onClick={() => setActiveTab('run')}>
          Run
        </button>
        <button className={`tab-button${activeTab === 'history' ? ' active' : ''}`} onClick={() => setActiveTab('history')}>
          History {history.length > 0 && <span className="tab-badge">{history.length}</span>}
        </button>
      </nav>

      {activeTab === 'run' && (
        <div className="run-shell">
          <div className="run-main">
            <CollapsiblePanel
              id="trigger"
              title="Trigger Automation"
              defaultExpanded={true}
              expandedPanels={expandedPanels}
              togglePanel={togglePanel}
            >
              <form onSubmit={triggerOrchestration} className="form-grid">
                <label>
                  Jira Ticket ID
                  <input value={ticket} onChange={(e) => setTicket(e.target.value)} placeholder="PROJ-123" required />
                </label>

                <label>
                  Reviewer (GitHub username)
                  <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="teammate-name" />
                </label>

                <label>
                  Repository (owner/repo or URL)
                  <input value={repository} onChange={(e) => setRepository(e.target.value)} placeholder="owner/repo" required />
                </label>

                <button type="submit" disabled={isRunning}>
                  {isRunning ? 'Running…' : 'Run Agentic Flow'}
                </button>
              </form>
            </CollapsiblePanel>

            {showFlow && <FlowDiagram steps={FLOW_STEPS} progress={progress} jobStatus={jobStatus} />}

            {error && <section className="panel error">{error}</section>}

            {result && (
              <section className="panel result">
                <CollapsiblePanel
                  id="result-summary"
                  title="Run Result"
                  defaultExpanded={true}
                  expandedPanels={expandedPanels}
                  togglePanel={togglePanel}
                >
                  <p>Branch: {result.branch_name}</p>
                  <p>
                    PR:{' '}
                    <a href={result.pull_request_url} target="_blank" rel="noreferrer">
                      {result.pull_request_url}
                    </a>
                  </p>
                </CollapsiblePanel>

                {result.usage && (
                  <CollapsiblePanel id="usage" title="Copilot Usage" expandedPanels={expandedPanels} togglePanel={togglePanel}>
                    {renderUsageSummary(result.usage)}
                  </CollapsiblePanel>
                )}

                {result.copilot_notes?.length > 0 && (
                  <CollapsiblePanel id="copilot-notes" title="Copilot CLI Suggestions" expandedPanels={expandedPanels} togglePanel={togglePanel}>
                    <ul>
                      {result.copilot_notes.map((note, idx) => (
                        <li key={idx}>{note}</li>
                      ))}
                    </ul>
                  </CollapsiblePanel>
                )}

                <CollapsiblePanel id="steps" title="Execution Steps" expandedPanels={expandedPanels} togglePanel={togglePanel}>
                  <ul>
                    {result.steps.map((step) => (
                      <li key={step.name}>
                        <strong>{step.name}</strong> - {step.status}
                        {step.details ? ` (${step.details})` : ''}
                      </li>
                    ))}
                  </ul>
                </CollapsiblePanel>
              </section>
            )}
          </div>

          <aside className="run-aside">
            <section className="panel side-panel">
              <div className="side-panel-header">
                <h2>Execution Pulse</h2>
                <span className={`status-badge status-${jobStatus === 'success' ? 'success' : jobStatus === 'failed' ? 'failed' : 'queued'}`}>
                  {JOB_STATUS_LABELS[jobStatus] || jobStatus}
                </span>
              </div>
              <div className="quick-stats">
                <div className="quick-stat-card">
                  <span className="quick-stat-value">{completedSteps}</span>
                  <span className="quick-stat-label">Completed Stages</span>
                </div>
                <div className="quick-stat-card">
                  <span className="quick-stat-value">{history.length}</span>
                  <span className="quick-stat-label">Persisted Runs</span>
                </div>
              </div>
              <div className="status-detail-list">
                <div>
                  <span className="status-detail-label">Current Repository</span>
                  <strong>{repository}</strong>
                </div>
                <div>
                  <span className="status-detail-label">Latest Stage</span>
                  <strong>{latestProgress?.name || 'awaiting_trigger'}</strong>
                </div>
                <div>
                  <span className="status-detail-label">Copilot Auth Source</span>
                  <strong>{copilotAuthSource || 'not checked yet'}</strong>
                </div>
              </div>
            </section>

            <section className="panel side-panel auth-guidance-panel">
              <h2>Copilot Auth Guidance</h2>
              <p>Headless runs should use a dedicated <strong>COPILOT_GITHUB_TOKEN</strong> with the <strong>Copilot Requests</strong> permission enabled.</p>
              <p>If that variable is absent, the backend now falls back to the OAuth token from <strong>gh auth token</strong> before using <strong>GITHUB_TOKEN</strong>.</p>
            </section>
          </aside>
        </div>
      )}

      {activeTab === 'history' && (
        <section className="panel">
          <h2>Orchestration History</h2>
          {history.length === 0 ? (
            <p>No orchestration runs yet.</p>
          ) : (
            <div className="history-list">
              {history.map((entry) => (
                <div key={entry.id} className={`history-entry history-${entry.status}`}>
                  {(() => {
                    const flow = computeFlowProgress(entry)
                    return (
                      <div className="history-flow-metric">
                        <span className="history-flow-label">Flow Progress</span>
                        <strong>{flow.done}/{flow.total}</strong>
                      </div>
                    )
                  })()}
                  <div className="history-header">
                    <strong>
                      {buildJiraLink(entry) ? (
                        <a href={buildJiraLink(entry)} target="_blank" rel="noreferrer" className="ticket-link">
                          {entry.request?.jira_ticket_id || '-'}
                        </a>
                      ) : (
                        entry.request?.jira_ticket_id || '-'
                      )}
                    </strong>{' '}
                    on <code>{entry.request?.repository || '-'}</code>
                    <span className={`status-badge status-${entry.status}`}>{entry.status}</span>
                  </div>
                  <div className="history-meta">
                    <small>{new Date(entry.finished_at || entry.created_at).toLocaleString()}</small>
                  </div>
                  {entry.error && <div className="history-error">{entry.error}</div>}
                  {entry.result && (
                    <div className="history-result">
                      <a href={entry.result.pull_request_url} target="_blank" rel="noreferrer">
                        View PR
                      </a>
                      {entry.result.usage && (
                        <div className="history-credits">
                          Changes +{formatInt(entry.result.usage?.changes?.added)} -{formatInt(entry.result.usage?.changes?.removed)}
                          {' '}| Credits {formatCredits(entry.result.usage.ai_credits_used)}
                          {entry.result.usage?.ai?.elapsed_text ? ` (${entry.result.usage.ai.elapsed_text})` : ''}
                          {' '}| Cost ${formatCost(entry.result.usage.estimated_cost_usd)}
                        </div>
                      )}

                      {entry.result.artifacts?.length > 0 && (
                        <div className="history-artifacts">
                          {entry.result.artifacts.map((artifact) => (
                            <button
                              type="button"
                              className="artifact-link"
                              key={artifact.path}
                              onClick={() => setSelectedArtifact(artifact)}
                            >
                              {artifact.path}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {entry.result?.steps?.length > 0 && (
                    <details className="history-collapsible">
                      <summary>Steps</summary>
                      <ul>
                        {entry.result.steps.map((step) => (
                          <li key={`${entry.id}-${step.name}-${step.status}`}>
                            <strong>{step.name}</strong> - {step.status}
                            {step.details ? ` (${step.details})` : ''}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}

                  <details className="history-collapsible history-logs" open={entry.status === 'running' || entry.status === 'queued'}>
                    <summary>Flow Diagram Steps</summary>
                    <HistoryStepDiagram entry={entry} />
                  </details>

                  {(entry.status === 'running' || entry.status === 'queued' || entry.progress?.length > 0) && (
                    <details className="history-collapsible history-logs">
                      <summary>Raw Logs and Stages</summary>
                      <ul>
                        {(entry.progress || []).map((log, idx) => (
                          <li key={`${entry.id}-log-${idx}`}>
                            <strong>{log.name}</strong> - {log.status}
                            {log.details ? ` (${log.details})` : ''}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {selectedArtifact && (
        <div className="artifact-modal-backdrop" onClick={() => setSelectedArtifact(null)}>
          <div className="artifact-modal" onClick={(event) => event.stopPropagation()}>
            <div className="artifact-modal-header">
              <h3>{selectedArtifact.path}</h3>
              <button type="button" onClick={() => setSelectedArtifact(null)}>Close</button>
            </div>
            <div className="artifact-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {selectedArtifact.content || ''}
              </ReactMarkdown>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
