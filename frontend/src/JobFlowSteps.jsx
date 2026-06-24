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
    const key = normalizeStepKey(item.name)
    statusMap[key] = item.status || statusMap[key] || 'idle'
  })
  return statusMap
}

export const computeFlowProgress = (entry) => {
  const statuses = collectHistoryStepStatus(entry)
  const done = FLOW_STEPS.filter((step) => {
    const status = statuses[step.key]
    return status === 'success' || status === 'skipped'
  }).length
  return { done, total: FLOW_STEPS.length }
}

export default function JobFlowSteps({ entry, idPrefix = 'flow' }) {
  const statuses = collectHistoryStepStatus(entry)

  return (
    <div className="history-step-diagram" aria-label="Job flow steps">
      {FLOW_STEPS.map((step) => {
        const status = statuses[step.key] || 'idle'
        return (
          <div key={`${idPrefix}-${step.key}`} className={`history-step history-step-${status}`}>
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