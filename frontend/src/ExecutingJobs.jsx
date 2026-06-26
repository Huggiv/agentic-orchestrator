import { useEffect, useState } from 'react'
import JobFlowSteps, { buildLogRows, RawLogsTable } from './JobFlowSteps'

const parseApiPayload = (raw) => {
  if (!raw) return {}
  try {
    return JSON.parse(raw)
  } catch {
    return { detail: raw }
  }
}

export default function ExecutingJobs({ runningJobs = [], onJobComplete }) {
  const [jobDetails, setJobDetails] = useState({})
  const [cancelState, setCancelState] = useState({})
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
                          : '#d8ebf8',
                  color:
                    details.status === 'success'
                      ? '#155724'
                      : details.status === 'failed'
                        ? '#721c24'
                        : details.status === 'cancelled'
                          ? '#92400e'
                          : '#0a4f74',
                }}
              >
                {details.status}
              </div>
              {(details.status === 'queued' || details.status === 'running') && (
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

            {details.error && (
              <div style={{ padding: '0.25rem 0.6rem', background: '#450a0a25', border: '1px solid #ef444440', borderRadius: '6px', marginBottom: '0.5rem', color: '#f87171', fontSize: '0.78rem', fontStyle: 'italic' }}>
                ⚠ Failed — click the highlighted step below for details
              </div>
            )}

            <JobFlowSteps
              idPrefix={job.id}
              entry={{
                id: job.id,
                status: details.status,
                progress,
                result: details.result,
                error: details.error,
              }}
              onFailedStepClick={(stepKey) => setHighlightedSteps((prev) => ({ ...prev, [job.id]: stepKey }))}
            />

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
