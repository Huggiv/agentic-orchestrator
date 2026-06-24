import { useEffect, useState } from 'react'
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

  useEffect(() => {
    if (!Array.isArray(runningJobs) || runningJobs.length === 0) return

    const interval = setInterval(async () => {
      for (const job of runningJobs) {
        if (!jobDetails[job.id]) {
          setJobDetails((prev) => ({ ...prev, [job.id]: { status: 'queued', progress: [] } }))
        }

        const statusResponse = await fetch(`/api/orchestrate/${job.id}`)
        const statusRaw = await statusResponse.text()
        const statusData = parseApiPayload(statusRaw)

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

          if (statusData.status === 'success' || statusData.status === 'failed') {
            if (onJobComplete) {
              onJobComplete(job.id)
            }
          }
        }
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [runningJobs, jobDetails, onJobComplete])

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
              <div
                style={{
                  display: 'inline-block',
                  padding: '0.25rem 0.6rem',
                  borderRadius: '12px',
                  fontSize: '0.75rem',
                  fontWeight: '700',
                  textTransform: 'uppercase',
                  background: details.status === 'success' ? '#d4edda' : details.status === 'failed' ? '#f8d7da' : '#d8ebf8',
                  color: details.status === 'success' ? '#155724' : details.status === 'failed' ? '#721c24' : '#0a4f74',
                }}
              >
                {details.status}
              </div>
            </div>

            {details.error && (
              <div style={{ padding: '0.5rem', background: '#fff3cd', border: '1px solid #ffc107', borderRadius: '4px', marginBottom: '1rem', color: '#856404', fontSize: '0.85rem' }}>
                {details.error}
              </div>
            )}

            {progress.length > 0 && <FlowDiagram steps={FLOW_STEPS} progress={progress} jobStatus={details.status} />}
          </div>
        )
      })}
    </div>
  )
}
