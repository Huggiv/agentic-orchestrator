import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ExecutingJobs from './ExecutingJobs'
import ChatConsole from './ChatConsole'
import JobFlowSteps, { computeFlowProgress, buildLogRows, RawLogsTable } from './JobFlowSteps'
import { getModels } from './services/models'

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

const normalizeCredits = (usage) => {
  if (!usage) return null
  if (usage.ai_credits_used !== null && usage.ai_credits_used !== undefined) {
    return Number(usage.ai_credits_used)
  }
  if (usage.total_nano_aiu !== null && usage.total_nano_aiu !== undefined) {
    return Number(usage.total_nano_aiu) / 1_000_000_000
  }
  return null
}

const formatCredits = (value) => {
  if (value === null || value === undefined) return '-'
  return Number(value).toFixed(4)
}

const formatCost = (value) => {
  if (value === null || value === undefined) return '-'
  return Number(value).toFixed(4)
}

const formatTokenCompact = (value) => {
  if (value === null || value === undefined) return '-'
  const amount = Number(value)
  if (Number.isNaN(amount)) return '-'

  const abs = Math.abs(amount)
  if (abs >= 1_000_000_000_000) return `${(amount / 1_000_000_000_000).toFixed(2)}T`
  if (abs >= 1_000_000) return `${(amount / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `${(amount / 1_000).toFixed(2)}K`
  return `${Math.round(amount)}`
}

const formatDurationHms = (seconds) => {
  if (seconds === null || seconds === undefined) return '-'
  const total = Math.max(0, Math.round(Number(seconds)))
  if (Number.isNaN(total)) return '-'

  const hrs = Math.floor(total / 3600)
  const mins = Math.floor((total % 3600) / 60)
  const secs = total % 60
  return `${String(hrs).padStart(2, '0')} hr ${String(mins).padStart(2, '0')} mins ${String(secs).padStart(2, '0')} secs`
}

const formatDurationCompact = (seconds) => {
  if (seconds === null || seconds === undefined) return '-'
  const total = Math.max(0, Math.round(Number(seconds)))
  if (Number.isNaN(total)) return '-'

  if (total >= 3600) {
    const hrs = Math.floor(total / 3600)
    const mins = Math.floor((total % 3600) / 60)
    const secs = total % 60
    return `${hrs} hr ${mins} mins ${secs} secs`
  }
  if (total >= 60) {
    const mins = Math.floor(total / 60)
    const secs = total % 60
    return `${mins} mins ${secs} secs`
  }
  return `${total} sec`
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
  const [availableAgents, setAvailableAgents] = useState(['SWE'])
  const [selectedAgent, setSelectedAgent] = useState('SWE')
  const [availableModels, setAvailableModels] = useState([])
  const [selectedModel, setSelectedModel] = useState('')   // '' = Auto (no --model flag)
  const [ticketError, setTicketError] = useState('')
  const [repository, setRepository] = useState('vittal-huggi_ADVNTST/v93000_telemetry_station')
  const [reviewer, setReviewer] = useState('')
  const [result, setResult] = useState(null)
  const [jobStatus, setJobStatus] = useState('idle')
  const [progress, setProgress] = useState([])
  const [error, setError] = useState('')
  const [jqlQuery, setJqlQuery] = useState('')
  const [jqlIssues, setJqlIssues] = useState([])
  const [jqlLoading, setJqlLoading] = useState(false)
  const [jqlError, setJqlError] = useState('')
  const [selectedJiraTickets, setSelectedJiraTickets] = useState([])
  const [history, setHistory] = useState([])
  const [runningJobs, setRunningJobs] = useState([])
  const [activeTab, setActiveTab] = useState('run')
  const [expandedPanels, setExpandedPanels] = useState({ trigger: true })
  const [selectedArtifact, setSelectedArtifact] = useState(null)
  const [historySearchFilter, setHistorySearchFilter] = useState('')
  // openRawLogs: Set of entry IDs whose "Raw Logs" panel is force-opened
  // highlightedSteps: { [entryId]: stepKey } — step that triggered the expand
  const [openRawLogs, setOpenRawLogs] = useState(new Set())
  const [highlightedSteps, setHighlightedSteps] = useState({})

  const loadHistory = async () => {
    const response = await fetch('/api/orchestrate/history?limit=30&include_progress=true')
    const raw = await response.text()
    const data = parseApiPayload(raw)
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to fetch orchestration history')
    }
    const items = Array.isArray(data.items) ? data.items : []
    setHistory(items)
    setRunningJobs(
      items
        .filter((entry) => entry.status === 'queued' || entry.status === 'running')
        .map((entry) => ({
          id: entry.id,
          jira_ticket_id: entry.request?.jira_ticket_id || '-',
          repository: entry.request?.repository || '-',
          selected_agent: entry.request?.selected_agent || 'SWE',
        }))
    )
  }

  const loadAgents = async () => {
    const response = await fetch('/api/agents')
    const raw = await response.text()
    const data = parseApiPayload(raw)
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to fetch available agents')
    }

    const items = Array.isArray(data.items) ? data.items.filter((item) => typeof item === 'string' && item) : []
    const nextAgents = items.length > 0 ? items : ['SWE']
    setAvailableAgents(nextAgents)
    setSelectedAgent((prev) => (nextAgents.includes(prev) ? prev : nextAgents[0]))
  }

  useEffect(() => {
    loadHistory().catch((err) => setError(err.message))
    loadAgents().catch(() => {
      setAvailableAgents(['SWE'])
      setSelectedAgent('SWE')
    })
    // Fetch models once at startup via the singleton — never re-fetches on re-renders.
    getModels().then((models) => {
      setAvailableModels(models)
      // Keep selectedModel as '' (Auto); never auto-select a specific model.
    })
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

  const handleJobComplete = (jobId) => {
    setRunningJobs((prev) => prev.filter((job) => job.id !== jobId))
    loadHistory().catch(() => undefined)
  }

  const handleDeleteHistoryEntry = async (entryId) => {
    if (!window.confirm('Delete this orchestration record and its workspace artifacts?')) return
    try {
      const response = await fetch(`/api/orchestrate/${entryId}`, { method: 'DELETE' })
      const raw = await response.text()
      const data = parseApiPayload(raw)
      if (!response.ok) throw new Error(data.detail || 'Failed to delete orchestration record')

      setHistory((prev) => prev.filter((entry) => entry.id !== entryId))
      setRunningJobs((prev) => prev.filter((job) => job.id !== entryId))
    } catch (err) {
      setError(err.message)
    }
  }

  const handlePurgeHistoryOlderThan30Days = async () => {
    if (!window.confirm('Delete all orchestration records older than 30 days and their workspace artifacts?')) return
    try {
      const response = await fetch('/api/orchestrate/history/purge?days=30', {
        method: 'POST',
      })
      const raw = await response.text()
      const data = parseApiPayload(raw)
      if (!response.ok) throw new Error(data.detail || 'Failed to purge old history')
      await loadHistory()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleChatQueuedJobs = (jobs) => {
    if (!Array.isArray(jobs) || jobs.length === 0) return
    const nextJobs = jobs.map((job) => ({
      id: job.job_id,
      jira_ticket_id: job.jira_ticket_id,
      repository,
      selected_agent: selectedAgent,
      selected_model: selectedModel || null,
    }))
    setRunningJobs((prev) => {
      const byId = new Map(prev.map((item) => [item.id, item]))
      nextJobs.forEach((item) => byId.set(item.id, item))
      return Array.from(byId.values())
    })
    loadHistory().catch(() => undefined)
    setActiveTab('executing')
  }

  const runTicketWorkflow = async (jiraTicketId) => {
    const response = await fetch('/api/orchestrate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        jira_ticket_id: jiraTicketId,
        selected_agent: selectedAgent,
        selected_model: selectedModel || null,
        repository,
        base_branch: 'development',
        reviewer: reviewer || null,
        commit_message: `feat(${jiraTicketId.toLowerCase()}): automated implementation`,
        change_plan: defaultPlan,
      }),
    })

    const raw = await response.text()
    const data = parseApiPayload(raw)
    if (!response.ok) throw new Error(data.detail || `Failed to trigger ${jiraTicketId}`)
    if (!data.job_id) throw new Error(`Missing job id for ${jiraTicketId}`)

    return {
      id: data.job_id,
      jira_ticket_id: jiraTicketId,
      repository,
      selected_agent: selectedAgent,
      selected_model: selectedModel || null,
    }
  }

  const searchJiraByJql = async (event) => {
    event.preventDefault()
    setJqlError('')
    setJqlLoading(true)
    try {
      const queryParam = encodeURIComponent(jqlQuery.trim())
      const response = await fetch(`/api/jira/issues?max_results=50&jql=${queryParam}`)
      const raw = await response.text()
      const data = parseApiPayload(raw)
      if (!response.ok) throw new Error(data.detail || 'Failed to search Jira issues')

      const issues = Array.isArray(data.issues) ? data.issues : []
      setJqlIssues(issues)
      setSelectedJiraTickets([])
    } catch (err) {
      setJqlError(err.message)
      setJqlIssues([])
      setSelectedJiraTickets([])
    } finally {
      setJqlLoading(false)
    }
  }

  const toggleJiraSelection = (ticketId) => {
    setSelectedJiraTickets((prev) =>
      prev.includes(ticketId)
        ? prev.filter((item) => item !== ticketId)
        : [...prev, ticketId]
    )
  }

  const selectAllJiraTickets = () => {
    setSelectedJiraTickets(jqlIssues.map((issue) => issue.key))
  }

  const clearAllJiraTickets = () => {
    setSelectedJiraTickets([])
  }

  const runSelectedJiraWorkflows = async () => {
    if (selectedJiraTickets.length === 0) {
      setJqlError('Select at least one Jira ticket.')
      return
    }
    setJqlError('')
    setJobStatus('queued')
    try {
      const jobs = await Promise.all(selectedJiraTickets.map((ticketId) => runTicketWorkflow(ticketId)))
      setRunningJobs((prev) => {
        const byId = new Map(prev.map((item) => [item.id, item]))
        jobs.forEach((item) => byId.set(item.id, item))
        return Array.from(byId.values())
      })
      setActiveTab('executing')
      setJobStatus('idle')
      setSelectedJiraTickets([])
    } catch (err) {
      setJobStatus('failed')
      setJqlError(err.message)
    }
  }

  const getIssueTitle = (issue) => issue.summary || issue.jira_summary || issue.title || '-'
  const getIssueStatus = (issue) => {
    const status = issue.status
    if (!status) return '-'
    if (typeof status === 'string') return status
    if (typeof status.name === 'string') return status.name
    return String(status)
  }

  // Jira ticket ID validation: must match pattern like PROJ-123
  const JIRA_TICKET_PATTERN = /^[A-Z][A-Z0-9_]+-\d+$/

  const validateTicket = (value) => {
    if (!value.trim()) return 'Jira Ticket ID is required.'
    if (!JIRA_TICKET_PATTERN.test(value.trim())) return 'Invalid format. Expected e.g. PROJ-123 (uppercase letters, digits, then a number).'
    return ''
  }

  const triggerOrchestration = async (event) => {
    event.preventDefault()
    const validationErr = validateTicket(ticket)
    if (validationErr) {
      setTicketError(validationErr)
      return
    }
    setTicketError('')
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
          selected_agent: selectedAgent,
          selected_model: selectedModel || null,
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

      // Add job to running jobs list and switch to Executing Jobs tab
      const newJob = {
        id: jobId,
        jira_ticket_id: ticket,
        repository,
        selected_agent: selectedAgent,
        selected_model: selectedModel || null,
      }
      setRunningJobs((prev) => [...prev, newJob])
      setActiveTab('executing')

      // Reset Run tab to idle/clean state for the next submission
      setJobStatus('idle')
      setTicket('')
      setReviewer('')
      setResult(null)
      setError('')
      setTicketError('')
    } catch (err) {
      setError(err.message)
      setJobStatus('failed')
    }
  }

  const isRunning = jobStatus === 'queued' || jobStatus === 'running'

  const buildJiraLink = (entry) => {
    const ticketId = entry.request?.jira_ticket_id
    const jiraBaseUrl = entry.request?.jira_url
    if (!ticketId || !jiraBaseUrl) return null
    return `${jiraBaseUrl.replace(/\/$/, '')}/browse/${ticketId}`
  }

  const filteredHistory = history.filter((entry) => {
    const ticketId = (entry.request?.jira_ticket_id || '').toLowerCase()
    const repo = (entry.request?.repository || '').toLowerCase()
    const query = historySearchFilter.trim().toLowerCase()
    if (!query) return true
    return ticketId.includes(query) || repo.includes(query)
  })

  const renderUsageSummary = (usage) => {
    if (!usage) return null

    const changes = usage.changes || {}
    const tokens = usage.tokens || {}
    const ai = usage.ai || {}
    const sessionIds = Array.isArray(usage.session_ids) ? usage.session_ids : []
    const aiCredits = normalizeCredits(usage)

    return (
      <div className="usage-summary">
        <div className="usage-grid">
          <div className="usage-card">
            <span className="usage-value">{formatInt(changes.added)}</span>
            <span className="usage-label">Lines Added</span>
          </div>
          <div className="usage-card">
            <span className="usage-value">{formatInt(changes.removed)}</span>
            <span className="usage-label">Lines Removed</span>
          </div>
          <div className="usage-card">
            <span className="usage-value">{formatTokenCompact(tokens.total)}</span>
            <span className="usage-label">Tokens Total</span>
          </div>
          <div className="usage-card">
            <span className="usage-value">{formatTokenCompact(tokens.input)}</span>
            <span className="usage-label">Tokens Input</span>
          </div>
          <div className="usage-card">
            <span className="usage-value">{formatTokenCompact(tokens.output)}</span>
            <span className="usage-label">Tokens Output</span>
          </div>
          <div className="usage-card">
            <span className="usage-value">{formatTokenCompact(tokens.cached)}</span>
            <span className="usage-label">Tokens Cached</span>
          </div>
          <div className="usage-card usage-card--cost">
            <span className="usage-value">{formatCredits(aiCredits)}</span>
            <span className="usage-label">AI Credits</span>
          </div>
          <div className="usage-card usage-card--rate">
            <span className="usage-value">${formatCost(usage.estimated_cost_usd)}</span>
            <span className="usage-label">Estimated Cost</span>
          </div>
        </div>
        <p>
          Duration: <strong>{formatDurationHms(ai.duration_seconds)}</strong>
        </p>
        <p>
          Session Source: <strong>{usage.source || '-'}</strong>
        </p>
        <p>
          Session Log Found: <strong>{usage.session_log_found ? 'Yes' : 'No'}</strong>
        </p>
        {sessionIds.length > 0 && (
          <p>
            Sessions: <strong>{sessionIds.join(', ')}</strong>
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="page">
      {/* RevGenAI-style fixed topnav */}
      <header className="topnav">
        <div className="topnav-logo">
          <span className="topnav-rocket">⚡</span>
          <span className="topnav-brand">AgentFlow</span>
        </div>

        <nav className="topnav-tabs">
          <button
            className={`topnav-tab${activeTab === 'run' ? ' topnav-tab--active' : ''}`}
            onClick={() => setActiveTab('run')}
          >
            Run
          </button>
          <button
            className={`topnav-tab${activeTab === 'executing' ? ' topnav-tab--active' : ''}`}
            onClick={() => setActiveTab('executing')}
          >
            Executing
            {runningJobs.length > 0 && <span className="topnav-badge">{runningJobs.length}</span>}
          </button>
          <button
            className={`topnav-tab${activeTab === 'history' ? ' topnav-tab--active' : ''}`}
            onClick={() => setActiveTab('history')}
          >
            History
            {history.length > 0 && <span className="topnav-badge">{history.length}</span>}
          </button>
        </nav>

        <div className="topnav-right">
          <span className={`topnav-status topnav-status--${jobStatus}`}>
            {JOB_STATUS_LABELS[jobStatus] || jobStatus}
          </span>
        </div>
      </header>

      <div className="page-body">

      {activeTab === 'run' && (
        <div className="run-shell run-shell--single">
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
                  <input
                    value={ticket}
                    onChange={(e) => {
                      setTicket(e.target.value)
                      if (ticketError) setTicketError('')
                    }}
                    placeholder="PROJ-123"
                    required
                    style={ticketError ? { borderColor: '#dc3545' } : undefined}
                  />
                  {ticketError && <span style={{ color: '#dc3545', fontSize: '0.78rem', marginTop: '0.2rem', display: 'block' }}>{ticketError}</span>}
                </label>

                <label>
                  Agent
                  <select value={selectedAgent} onChange={(e) => setSelectedAgent(e.target.value)}>
                    {availableAgents.map((agent) => (
                      <option key={agent} value={agent}>{agent}</option>
                    ))}
                  </select>
                </label>

                <label>
                  Model
                  <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
                    <option value=''>Auto</option>
                    {availableModels.map((model) => (
                      <option key={model.id} value={model.id}>{model.name}</option>
                    ))}
                  </select>
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

            <CollapsiblePanel
              id="jira-search"
              title="Search Jira and Bulk Trigger"
              defaultExpanded={true}
              expandedPanels={expandedPanels}
              togglePanel={togglePanel}
            >
              <form onSubmit={searchJiraByJql} className="jira-search-form">
                <label>
                  JQL Query
                  <input
                    value={jqlQuery}
                    onChange={(e) => setJqlQuery(e.target.value)}
                    placeholder="project = DEMO AND status != DONE ORDER BY updated DESC"
                  />
                </label>
                <button type="submit" disabled={jqlLoading}>
                  {jqlLoading ? 'Searching...' : 'Search Jira'}
                </button>
                <button type="button" onClick={runSelectedJiraWorkflows} disabled={selectedJiraTickets.length === 0}>
                  Run Selected ({selectedJiraTickets.length})
                </button>
              </form>

              {jqlIssues.length > 0 && (
                <div className="jira-selection-actions">
                  <button type="button" onClick={selectAllJiraTickets}>
                    Select All
                  </button>
                  <button type="button" onClick={clearAllJiraTickets}>
                    Clear All
                  </button>
                </div>
              )}

              {jqlError && <p className="jira-search-error">{jqlError}</p>}

              {jqlIssues.length > 0 && (
                <div className="jira-results-table-wrap">
                  <table className="jira-results-table">
                    <thead>
                      <tr>
                        <th>Select</th>
                        <th>Jira ID</th>
                        <th>Title</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {jqlIssues.map((issue) => (
                        <tr key={issue.key}>
                          <td>
                            <input
                              type="checkbox"
                              checked={selectedJiraTickets.includes(issue.key)}
                              onChange={() => toggleJiraSelection(issue.key)}
                            />
                          </td>
                          <td>{issue.key}</td>
                          <td>{getIssueTitle(issue)}</td>
                          <td>{getIssueStatus(issue)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CollapsiblePanel>

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
        </div>
      )}

      {activeTab === 'executing' && (
        <section className="panel">
          <ExecutingJobs runningJobs={runningJobs} onJobComplete={handleJobComplete} />
        </section>
      )}

      {activeTab === 'history' && (
        <section className="panel">
          <div className="panel-title-row">
            <h2>Orchestration History</h2>
            <button
              type="button"
              className="history-purge-btn"
              onClick={handlePurgeHistoryOlderThan30Days}
            >
              Purge Older Than 30 Days
            </button>
          </div>

          <div className="history-filters">
            <label>
              Search
              <input
                value={historySearchFilter}
                onChange={(e) => setHistorySearchFilter(e.target.value)}
                placeholder="Search by Jira ticket or repository"
              />
            </label>
          </div>

          {history.length === 0 ? (
            <p>No orchestration runs yet.</p>
          ) : filteredHistory.length === 0 ? (
            <p>No history entries match the applied filters.</p>
          ) : (
            <div className="history-list">
              {filteredHistory.map((entry) => (
                <div key={entry.id} className={`history-entry history-${entry.status}`}>
                  <div className="history-top-row">
                    {(() => {
                      const flow = computeFlowProgress(entry)
                      return (
                        <div className="history-flow-metric">
                          <span className="history-flow-label">Flow Progress</span>
                          <strong>{flow.done}/{flow.total}</strong>
                        </div>
                      )
                    })()}
                    <div className="history-trigger-time">
                      Triggered: {new Date(entry.created_at).toLocaleString()}
                    </div>
                  </div>
                  <div className="history-header">
                    <div className="history-header-main">
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
                      {entry.result?.pull_request_url && (
                        <a href={entry.result.pull_request_url} target="_blank" rel="noreferrer" className="history-pr-link">
                          View PR
                        </a>
                      )}
                      <span style={{ display: 'block', marginTop: '0.25rem', fontSize: '0.78rem', color: '#4e6c80' }}>
                        Agent: <strong>{entry.request?.selected_agent || 'SWE'}</strong>
                        {' · '}Model: <strong>{entry.request?.selected_model || 'Auto'}</strong>
                      </span>
                    </div>
                    <div className="history-header-right">
                      <span className="history-duration-pill">
                        Duration: {formatDurationCompact(entry.result?.usage?.ai?.duration_seconds)}
                      </span>
                      <span className={`status-badge status-${entry.status}`}>{entry.status}</span>
                      <button
                        type="button"
                        className="history-delete-btn"
                        onClick={() => handleDeleteHistoryEntry(entry.id)}
                        title="Delete record"
                        aria-label="Delete orchestration record"
                      >
                        x
                      </button>
                    </div>
                  </div>
                  {entry.status === 'failed' && entry.error && (
                    <div className="history-error" style={{ fontStyle: 'italic', fontSize: '0.78rem' }}>
                      ⚠ Failed — click the highlighted step in the flow diagram for details
                    </div>
                  )}
                  {entry.result && (
                    <div className="history-result">
                      {entry.result.usage && (
                        <details className="history-collapsible" open={entry.status === 'running' || entry.status === 'queued'}>
                          <summary>Changes and Usage</summary>
                          <div className="history-credits">
                            <table className="history-credits-table">
                              <tbody>
                                <tr className="history-credits-highlight">
                                  <th scope="row">Changes</th>
                                  <td>+{formatInt(entry.result.usage?.changes?.added)} / -{formatInt(entry.result.usage?.changes?.removed)}</td>
                                </tr>
                                <tr className="history-credits-highlight">
                                  <th scope="row">Cost</th>
                                  <td>${formatCost(entry.result.usage.estimated_cost_usd)}</td>
                                </tr>
                                <tr>
                                  <th scope="row">AI Credits</th>
                                  <td>{formatCredits(normalizeCredits(entry.result.usage))}</td>
                                </tr>
                                <tr>
                                  <th scope="row">Tokens</th>
                                  <td>
                                    Total {formatTokenCompact(entry.result.usage?.tokens?.total)}
                                    {' '}({formatTokenCompact(entry.result.usage?.tokens?.input)} In, {formatTokenCompact(entry.result.usage?.tokens?.output)} Out, {formatTokenCompact(entry.result.usage?.tokens?.cached)} cached)
                                  </td>
                                </tr>
                                <tr>
                                  <th scope="row">Duration</th>
                                  <td>{formatDurationHms(entry.result.usage?.ai?.duration_seconds)}</td>
                                </tr>
                                {entry.result.usage?.session_ids?.length > 0 && (
                                  <tr>
                                    <th scope="row">Session</th>
                                    <td>{entry.result.usage.session_ids.join(', ')}</td>
                                  </tr>
                                )}
                              </tbody>
                            </table>
                          </div>
                        </details>
                      )}

                      {entry.result.artifacts?.length > 0 && (
                        <details className="history-collapsible">
                          <summary>Artifacts</summary>
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
                        </details>
                      )}
                    </div>
                  )}

                  <details className="history-collapsible history-logs" open={entry.status === 'running' || entry.status === 'queued'}>
                    <summary>Flow Diagram Steps</summary>
                    <JobFlowSteps
                      idPrefix={entry.id}
                      entry={entry}
                      onFailedStepClick={(stepKey) => {
                        setOpenRawLogs((prev) => new Set([...prev, entry.id]))
                        setHighlightedSteps((prev) => ({ ...prev, [entry.id]: stepKey }))
                      }}
                    />
                  </details>

                  {(entry.status === 'running' || entry.status === 'queued' || entry.progress?.length > 0) && (
                    <details
                      className="history-collapsible history-logs"
                      open={openRawLogs.has(entry.id) || entry.status === 'running' || entry.status === 'queued'}
                      onToggle={(e) => {
                        if (!e.target.open) {
                          setOpenRawLogs((prev) => { const next = new Set(prev); next.delete(entry.id); return next })
                          setHighlightedSteps((prev) => { const next = { ...prev }; delete next[entry.id]; return next })
                        }
                      }}
                    >
                      <summary>Raw Logs and Stages</summary>
                      <RawLogsTable rows={buildLogRows(entry)} highlightKey={highlightedSteps[entry.id] || null} />
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      </div>

      {/* Artifact modal (outside page-body so it overlays everything) */}
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

      <ChatConsole
        repository={repository}
        setRepository={setRepository}
        reviewer={reviewer}
        setReviewer={setReviewer}
        selectedAgent={selectedAgent}
        setSelectedAgent={setSelectedAgent}
        selectedModel={selectedModel}
        setSelectedModel={setSelectedModel}
        availableAgents={availableAgents}
        availableModels={availableModels}
        onJobsQueued={handleChatQueuedJobs}
      />

      {/* Footer */}
      <footer className="app-footer">
        <span className="app-footer-brand">⚡ AgentFlow</span>
        <span className="app-footer-version">v1.0.0 &nbsp;·&nbsp; FastAPI + React 19</span>
        <span className="app-footer-copy">© {new Date().getFullYear()} RevGenAI · All rights reserved</span>
      </footer>
    </div>
  )
}
