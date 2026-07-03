import { useEffect, useState } from 'react'
import JobFlowSteps, { buildLogRows, RawLogsTable } from './JobFlowSteps'
import { approveJob, pauseJob, rejectJob, resumeJob } from './services/orchestrate'

const parseApiPayload = (raw) => {
  if (!raw) return {}
  try {
    return JSON.parse(raw)
  } catch {
    return { detail: raw }
  }
}

const resolveJobArtifacts = (details) => {
  const resultArtifacts = details?.result?.artifacts
  if (Array.isArray(resultArtifacts) && resultArtifacts.length > 0) {
    return resultArtifacts
  }

  const progress = Array.isArray(details?.progress) ? details.progress : []
  for (let idx = progress.length - 1; idx >= 0; idx -= 1) {
    const event = progress[idx]
    if (event?.name !== 'view_artifacts') continue
    if (!Array.isArray(event?.artifacts)) continue
    if (event.artifacts.length === 0) continue
    return event.artifacts
  }
  return []
}

const APPROVAL_CHECKPOINTS = new Set(['create_pr', 'publish_review_comments'])

const resolveAllowedActions = (details) => {
  const serverActions = Array.isArray(details?.allowed_actions) ? details.allowed_actions : []
  if (serverActions.length > 0) return serverActions

  const currentStep = String(details?.current_step || '').trim()
  if (details?.status === 'blocked_approval' && APPROVAL_CHECKPOINTS.has(currentStep)) {
    return ['approve', 'reject', 'cancel']
  }

  if (details?.status === 'blocked_approval') {
    return ['cancel']
  }

  return []
}

export default function ExecutingJobs({
  runningJobs = [],
  onJobComplete,
  onArtifactOpen,
  onArtifactDownload,
}) {
  const [jobDetails, setJobDetails] = useState({})
  const [cancelState, setCancelState] = useState({})
  const [actionState, setActionState] = useState({})
  // highlightedSteps: { [jobId]: stepKey } — set when a failed step card is clicked
  const [highlightedSteps, setHighlightedSteps] = useState({})

  const cancelJob = async (jobId) => {
    setCancelState((prev) => ({ ...prev, [jobId]: 'cancelling' }))
    try {
      const response = await fetch(`/api/orchestrate/${jobId}/cancel`, {
        method: 'POST',
      })
      const raw = await response.text()
      const data = parseApiPayload(raw)
      if (!response.ok) throw new Error(data.detail || 'Failed to cancel job')
      setCancelState((prev) => ({ ...prev, [jobId]: data.status || 'cancelling' }))
      setJobDetails((prev) => ({
        ...prev,
        [jobId]: {
          ...prev[jobId],
          status: data.status === 'cancelled' ? 'cancelled' : prev[jobId]?.status || 'running',
        },
      }))
    } catch {
      setCancelState((prev) => ({ ...prev, [jobId]: 'error' }))
    }
  }

  const executeAction = async (jobId, action) => {
    setActionState((prev) => ({ ...prev, [jobId]: action }))
    try {
      if (action === 'pause') await pauseJob(jobId)
      if (action === 'resume') await resumeJob(jobId)
      if (action === 'approve') await approveJob(jobId)
      if (action === 'reject') await rejectJob(jobId)
    } catch {
      // Poll loop will surface backend status/error.
    } finally {
      setActionState((prev) => ({ ...prev, [jobId]: '' }))
    }
  }

  useEffect(() => {
    if (!Array.isArray(runningJobs) || runningJobs.length === 0) {
      return undefined
    }

    let active = true

    const poll = async () => {
      await Promise.all(
        runningJobs.map(async (job) => {
          try {
            const statusResponse = await fetch(`/api/orchestrate/${job.id}`)
            const statusRaw = await statusResponse.text()
            const statusData = parseApiPayload(statusRaw)

            if (!active) return

            if (statusResponse.ok) {
              setJobDetails((prev) => ({
                ...prev,
                [job.id]: {
                  status: statusData.status,
                  progress: statusData.progress || [],
                  step_logs: statusData.step_logs || [],
                  current_step: statusData.current_step || null,
                  next_step: statusData.next_step || null,
                  allowed_actions: statusData.allowed_actions || [],
                  error: statusData.error,
                  result: statusData.result,
                },
              }))

              if ((statusData.status === 'success' || statusData.status === 'failed' || statusData.status === 'cancelled') && onJobComplete) {
                onJobComplete(job.id)
              }
              return
            }

            setJobDetails((prev) => ({
              ...prev,
              [job.id]: {
                status: prev[job.id]?.status || 'running',
                progress: prev[job.id]?.progress || [],
                  step_logs: prev[job.id]?.step_logs || [],
                  current_step: prev[job.id]?.current_step || null,
                  next_step: prev[job.id]?.next_step || null,
                  allowed_actions: prev[job.id]?.allowed_actions || [],
                error: statusData.detail || 'Failed to fetch job status',
                result: prev[job.id]?.result,
              },
            }))
          } catch (error) {
            if (!active) return
            setJobDetails((prev) => ({
              ...prev,
              [job.id]: {
                status: prev[job.id]?.status || 'running',
                progress: prev[job.id]?.progress || [],
                  step_logs: prev[job.id]?.step_logs || [],
                  current_step: prev[job.id]?.current_step || null,
                  next_step: prev[job.id]?.next_step || null,
                  allowed_actions: prev[job.id]?.allowed_actions || [],
                error: error?.message || 'Failed to fetch job status',
                result: prev[job.id]?.result,
              },
            }))
          }
        })
      )
    }

    setJobDetails((prev) => {
      const next = { ...prev }
      runningJobs.forEach((job) => {
        if (!next[job.id]) {
          next[job.id] = { status: 'queued', progress: [] }
        }
      })
      return next
    })

    poll()
    const interval = setInterval(poll, 2000)

    return () => {
      active = false
      clearInterval(interval)
    }
  }, [runningJobs, onJobComplete])

  if (runningJobs.length === 0) {
    return (
      <div className="panel">
        <p>No running jobs.</p>
      </div>
    )
  }

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      {runningJobs.map((job) => {
        const details = jobDetails[job.id] || { status: 'queued', progress: [] }
        const progress = details.progress || []
        const artifacts = resolveJobArtifacts(details)
        const allowedActions = resolveAllowedActions(details)
        const pendingAction = actionState[job.id]

        return (
          <div key={job.id} className="panel">
            <div style={{ marginBottom: '1rem' }}>
              <h3 style={{ margin: '0 0 0.5rem 0', fontSize: '0.95rem', color: '#1f4156' }}>
                {job.jira_ticket_id} on {job.repository}
              </h3>
              <p style={{ margin: '0 0 0.45rem 0', fontSize: '0.8rem', color: '#4e6c80' }}>
                Agent: <strong>{job.selected_agent || 'SWE'}</strong>
                {' · '}Model: <strong>{job.selected_model || 'Auto'}</strong>
              </p>
              <div
                style={{
                  display: 'inline-block',
                  padding: '0.25rem 0.6rem',
                  borderRadius: '12px',
                  fontSize: '0.75rem',
                  fontWeight: '700',
                  textTransform: 'uppercase',
                  background:
                    details.status === 'success'
                      ? '#d4edda'
                      : details.status === 'failed'
                        ? '#f8d7da'
                        : details.status === 'cancelled'
                          ? '#fef3c7'
                          : details.status === 'paused'
                            ? '#e5e7eb'
                            : details.status === 'blocked_approval'
                              ? '#ffedd5'
                          : '#d8ebf8',
                  color:
                    details.status === 'success'
                      ? '#155724'
                      : details.status === 'failed'
                        ? '#721c24'
                        : details.status === 'cancelled'
                          ? '#92400e'
                          : details.status === 'paused'
                            ? '#374151'
                            : details.status === 'blocked_approval'
                              ? '#9a3412'
                          : '#0a4f74',
                }}
              >
                {details.status}
              </div>
              {allowedActions.includes('pause') && (
                <button
                  type="button"
                  onClick={() => executeAction(job.id, 'pause')}
                  disabled={pendingAction === 'pause'}
                  style={{
                    marginLeft: '0.6rem',
                    padding: '0.24rem 0.62rem',
                    borderRadius: '12px',
                    border: '1px solid #4b5563',
                    background: '#f3f4f6',
                    color: '#374151',
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    cursor: pendingAction === 'pause' ? 'default' : 'pointer',
                    boxShadow: 'none',
                  }}
                >
                  {pendingAction === 'pause' ? 'Pausing...' : 'Pause'}
                </button>
              )}
              {allowedActions.includes('resume') && (
                <button
                  type="button"
                  onClick={() => executeAction(job.id, 'resume')}
                  disabled={pendingAction === 'resume'}
                  style={{
                    marginLeft: '0.6rem',
                    padding: '0.24rem 0.62rem',
                    borderRadius: '12px',
                    border: '1px solid #16a34a',
                    background: '#dcfce7',
                    color: '#166534',
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    cursor: pendingAction === 'resume' ? 'default' : 'pointer',
                    boxShadow: 'none',
                  }}
                >
                  {pendingAction === 'resume' ? 'Resuming...' : 'Resume'}
                </button>
              )}
              {allowedActions.includes('approve') && (
                <button
                  type="button"
                  onClick={() => executeAction(job.id, 'approve')}
                  disabled={pendingAction === 'approve'}
                  style={{
                    marginLeft: '0.6rem',
                    padding: '0.24rem 0.62rem',
                    borderRadius: '12px',
                    border: '1px solid #16a34a',
                    background: '#dcfce7',
                    color: '#166534',
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    cursor: pendingAction === 'approve' ? 'default' : 'pointer',
                    boxShadow: 'none',
                  }}
                >
                  {pendingAction === 'approve' ? 'Approving...' : 'Approve'}
                </button>
              )}
              {allowedActions.includes('reject') && (
                <button
                  type="button"
                  onClick={() => executeAction(job.id, 'reject')}
                  disabled={pendingAction === 'reject'}
                  style={{
                    marginLeft: '0.6rem',
                    padding: '0.24rem 0.62rem',
                    borderRadius: '12px',
                    border: '1px solid #dc2626',
                    background: '#fee2e2',
                    color: '#991b1b',
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    cursor: pendingAction === 'reject' ? 'default' : 'pointer',
                    boxShadow: 'none',
                  }}
                >
                  {pendingAction === 'reject' ? 'Rejecting...' : 'Reject'}
                </button>
              )}
              {allowedActions.includes('cancel') && (
                <button
                  type="button"
                  onClick={() => cancelJob(job.id)}
                  disabled={cancelState[job.id] === 'cancelling'}
                  style={{
                    marginLeft: '0.6rem',
                    padding: '0.24rem 0.62rem',
                    borderRadius: '12px',
                    border: '1px solid #ef4444',
                    background: '#fee2e2',
                    color: '#991b1b',
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    cursor: cancelState[job.id] === 'cancelling' ? 'default' : 'pointer',
                    boxShadow: 'none',
                  }}
                >
                  {cancelState[job.id] === 'cancelling' ? 'Cancelling...' : 'Cancel'}
                </button>
              )}
            </div>

            {(details.current_step || details.next_step) && (
              <p style={{ margin: '0.15rem 0 0.4rem 0', fontSize: '0.75rem', color: '#4e6c80' }}>
                Current: <strong>{details.current_step || '-'}</strong>
                {' · '}Next: <strong>{details.next_step || '-'}</strong>
              </p>
            )}

            {details.status === 'blocked_approval' && (
              <div style={{ padding: '0.25rem 0.6rem', background: '#451a0325', border: '1px solid #f59e0b50', borderRadius: '6px', marginBottom: '0.5rem', color: '#fdba74', fontSize: '0.78rem' }}>
                Approval required to continue this checkpoint.
              </div>
            )}

            {details.error && (
              <div style={{ padding: '0.25rem 0.6rem', background: '#450a0a25', border: '1px solid #ef444440', borderRadius: '6px', marginBottom: '0.5rem', color: '#f87171', fontSize: '0.78rem', fontStyle: 'italic' }}>
                ⚠ Failed — click the highlighted step below for details
              </div>
            )}

            <JobFlowSteps
              idPrefix={job.id}
              entry={{
                id: job.id,
                selected_agent: job.selected_agent,
                jira_ticket_id: job.jira_ticket_id,
                status: details.status,
                progress,
                current_step: details.current_step,
                next_step: details.next_step,
                result: details.result,
                error: details.error,
              }}
              onFailedStepClick={(stepKey) => setHighlightedSteps((prev) => ({ ...prev, [job.id]: stepKey }))}
            />

            {artifacts.length > 0 && (
              <details className="history-collapsible" style={{ marginTop: '0.75rem' }}>
                <summary>Artifacts</summary>
                <div className="history-artifacts">
                  {artifacts.map((artifact) => (
                    <div className="artifact-actions" key={artifact.path}>
                      <button
                        type="button"
                        className="artifact-link"
                        onClick={() => onArtifactOpen?.(artifact)}
                      >
                        {artifact.path}
                      </button>
                      <button
                        type="button"
                        className="artifact-download-btn"
                        onClick={() => onArtifactDownload?.(artifact)}
                        disabled={!String(artifact.content || '').trim()}
                        title={!String(artifact.content || '').trim() ? 'Artifact has no content to download' : 'Download artifact'}
                        aria-label={`Download ${artifact.path}`}
                      >
                        Download
                      </button>
                    </div>
                  ))}
                </div>
              </details>
            )}

            {(progress.length > 0 || highlightedSteps[job.id]) && (
              <details
                className="history-collapsible history-logs"
                style={{ marginTop: '0.75rem' }}
                open={!!highlightedSteps[job.id]}
                onToggle={(e) => {
                  if (!e.target.open) setHighlightedSteps((prev) => { const next = { ...prev }; delete next[job.id]; return next })
                }}
              >
                <summary>Raw Logs and Stages</summary>
                <RawLogsTable
                  rows={buildLogRows({ status: details.status, progress, result: details.result, error: details.error })}
                  highlightKey={highlightedSteps[job.id] || null}
                />
              </details>
            )}
          </div>
        )
      })}
    </div>
  )
}
