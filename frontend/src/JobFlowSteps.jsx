import { Fragment } from 'react'
import { StageIcon } from './FlowDiagram'

export const FLOW_STEPS = [
  { key: 'clone_repository', label: 'Clone Repo' },
  { key: 'auth_setup', label: 'Auth Setup' },
  { key: 'prepare_branch', label: 'Prepare Branch' },
  { key: 'read_jira', label: 'Read Jira' },
  { key: 'agentic_implementation', label: 'Agentic Impl' },
  { key: 'commit_changes', label: 'Commit Changes' },
  { key: 'push_branch', label: 'Push Branch' },
  { key: 'create_pr', label: 'Create PR' },
]

const STEP_ALIASES = {
  create_and_checkout_branch: 'prepare_branch',
  read_jira_issue: 'read_jira',
  copilot_agentic_plan: 'agentic_implementation',
}

const STEP_STATUS_LABELS = {
  success: 'Done',
  skipped: 'Skipped',
  failed: 'Failed',
  cancelled: 'Cancelled',
  running: 'Running',
  queued: 'Queued',
  idle: 'Idle',
}

const normalizeStepKey = (value) => STEP_ALIASES[value] || value

/**
 * Returns { statusMap, detailsMap } where detailsMap[stepKey] is the last
 * non-empty details string emitted for that step.
 */
const collectHistoryStepData = (entry) => {
  const statusMap = {}
  const detailsMap = {}

  ;(entry.progress || []).forEach((item) => {
    const key = normalizeStepKey(item.name)
    statusMap[key] = item.status || 'idle'
    if (item.details) detailsMap[key] = item.details
  })
  ;(entry.result?.steps || []).forEach((item) => {
    const key = normalizeStepKey(item.name)
    statusMap[key] = item.status || statusMap[key] || 'idle'
    if (item.details) detailsMap[key] = item.details
  })

  // When the overall job failed, ensure the last step that was started (but
  // never reached "success") is shown as failed so the diagram is accurate.
  if (entry.status === 'failed') {
    const hasExplicitFailure = Object.values(statusMap).some((s) => s === 'failed')
    if (!hasExplicitFailure) {
      // Find the last FLOW_STEPS key that started (running / queued / success)
      // but isn't success — mark it failed.
      let lastStartedKey = null
      for (const step of FLOW_STEPS) {
        if (statusMap[step.key] && statusMap[step.key] !== 'idle') {
          lastStartedKey = step.key
        }
      }
      if (lastStartedKey && statusMap[lastStartedKey] !== 'success') {
        statusMap[lastStartedKey] = 'failed'
      }
    }
  }

  if (entry.status === 'cancelled') {
    let lastStartedKey = null
    for (const step of FLOW_STEPS) {
      if (statusMap[step.key] && statusMap[step.key] !== 'idle') {
        lastStartedKey = step.key
      }
    }
    if (lastStartedKey && statusMap[lastStartedKey] !== 'success' && statusMap[lastStartedKey] !== 'skipped') {
      statusMap[lastStartedKey] = 'cancelled'
    }
  }

  return { statusMap, detailsMap }
}

// Legacy helper kept for computeFlowProgress callers.
const collectHistoryStepStatus = (entry) => collectHistoryStepData(entry).statusMap

/**
 * Collapses the raw progress event stream into one row per step (last-wins),
 * infers "failed" for a step that was still running when the job failed,
 * and attaches entry.error to that step.
 *
 * Returns an array of { name, status, details, error? }.
 */
export function buildLogRows(entry) {
  const normalized = {}  // stepName → { name, status, details }
  const order = []

  ;(entry.progress || []).forEach((item) => {
    const key = STEP_ALIASES[item.name] || item.name
    if (!normalized[key]) order.push(key)
    normalized[key] = { name: item.name, status: item.status || 'idle', details: item.details || normalized[key]?.details || null }
  })

  // When the overall job failed, find the last step still in running/queued state
  // (no success event was emitted) and mark it failed.
  if (entry.status === 'failed') {
    let failedKey = null
    for (const key of order) {
      if (normalized[key].status === 'running' || normalized[key].status === 'queued') {
        failedKey = key
      }
    }
    if (!failedKey) {
      // All steps show success but job still failed — blame last step in order
      failedKey = order[order.length - 1] || null
    }
    if (failedKey && normalized[failedKey].status !== 'success' && normalized[failedKey].status !== 'skipped') {
      normalized[failedKey].status = 'failed'
      normalized[failedKey].error = entry.error || null
    }
  }

  if (entry.status === 'cancelled') {
    let cancelledKey = null
    for (const key of order) {
      if (normalized[key].status === 'running' || normalized[key].status === 'queued') {
        cancelledKey = key
      }
    }
    if (!cancelledKey) {
      cancelledKey = order[order.length - 1] || null
    }
    if (cancelledKey && normalized[cancelledKey].status !== 'success' && normalized[cancelledKey].status !== 'skipped') {
      normalized[cancelledKey].status = 'cancelled'
      normalized[cancelledKey].error = entry.error || null
    }
  }

  return order.map((key) => normalized[key])
}

export const computeFlowProgress = (entry) => {
  const statuses = collectHistoryStepStatus(entry)
  const done = FLOW_STEPS.filter((step) => {
    const status = statuses[step.key]
    return status === 'success' || status === 'skipped'
  }).length
  return { done, total: FLOW_STEPS.length }
}

export default function JobFlowSteps({ entry, idPrefix = 'flow', onFailedStepClick }) {
  const { statusMap } = collectHistoryStepData(entry)

  const handleStepClick = (step, status) => {
    if (status !== 'failed') return
    onFailedStepClick?.(step.key)
  }

  return (
    <div className="history-step-diagram" aria-label="Job flow steps">
      {FLOW_STEPS.map((step) => {
        const status = statusMap[step.key] || 'idle'
        const isFailed = status === 'failed'
        return (
          <div
            key={`${idPrefix}-${step.key}`}
            className={`history-step history-step-${status}${isFailed ? ' history-step-clickable' : ''}`}
            onClick={() => handleStepClick(step, status)}
            role={isFailed ? 'button' : undefined}
            tabIndex={isFailed ? 0 : undefined}
            onKeyDown={isFailed ? (e) => { if (e.key === 'Enter' || e.key === ' ') handleStepClick(step, status) } : undefined}
            title={isFailed ? 'Click to expand error below' : undefined}
          >
            <svg width="26" height="26" viewBox="0 0 28 28" role="img" aria-label={step.label}>
              <circle cx="14" cy="14" r="12" className="history-step-ring" />
              <g transform="translate(0,0)">
                <StageIcon stepKey={step.key} color="currentColor" />
              </g>
            </svg>
            <span className="history-step-label">
              {step.label}
              {isFailed && <span className="history-step-error-icon" aria-hidden="true"> ⚠</span>}
            </span>
            <span className="history-step-status">{STEP_STATUS_LABELS[status] || status}</span>
          </div>
        )
      })}
    </div>
  )
}

const STATUS_BADGE = {
  success: { bg: '#14532d25', color: '#4ade80', border: '#16a34a50', label: 'success' },
  failed:  { bg: '#450a0a50', color: '#f87171',  border: '#ef4444a0', label: 'failed'  },
  cancelled: { bg: '#78350f25', color: '#fbbf24', border: '#d9770640', label: 'cancelled' },
  running: { bg: '#1e3a5f40', color: '#60a5fa',  border: '#3b82f640', label: 'running' },
  queued:  { bg: '#1e3a5f40', color: '#60a5fa',  border: '#3b82f640', label: 'queued'  },
  skipped: { bg: '#78350f25', color: '#fbbf24',  border: '#d9770640', label: 'skipped' },
  idle:    { bg: 'transparent', color: '#94a3b8', border: '#33455530', label: 'idle'   },
}

/**
 * Renders the deduplicated, failure-annotated log rows produced by buildLogRows().
 * highlightKey: the step name (normalized) that should be auto-scrolled into view.
 */
export function RawLogsTable({ rows, highlightKey }) {
  if (!rows || rows.length === 0) return <p style={{ color: '#64748b', fontSize: '0.8rem', padding: '0.4rem 0' }}>No log events yet.</p>

  return (
    <table className="raw-logs-table">
      <thead>
        <tr>
          <th>Stage</th>
          <th>Status</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, idx) => {
          const badge = STATUS_BADGE[row.status] || STATUS_BADGE.idle
          const normalizedKey = STEP_ALIASES[row.name] || row.name
          const isHighlighted = highlightKey === normalizedKey && row.status === 'failed'
          const isFailed = row.status === 'failed'

          return (
            <Fragment key={idx}>
              <tr
                className={isFailed ? 'raw-logs-row-failed' : ''}
                style={isHighlighted ? { outline: '1px solid #ef4444a0' } : undefined}
              >
                <td className="raw-logs-stage">{row.name}</td>
                <td>
                  <span className="raw-logs-badge" style={{ background: badge.bg, color: badge.color, border: `1px solid ${badge.border}` }}>
                    {badge.label}
                  </span>
                </td>
                <td className="raw-logs-details">{row.details || '—'}</td>
              </tr>
              {isFailed && row.error && (
                <tr className="raw-logs-row-error">
                  <td colSpan={3}>
                    <pre className="raw-logs-error-pre">{row.error}</pre>
                  </td>
                </tr>
              )}
            </Fragment>
          )
        })}
      </tbody>
    </table>
  )
}