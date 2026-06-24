import { useEffect, useState } from 'react'
import JobFlowSteps from './JobFlowSteps'

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

              if ((statusData.status === 'success' || statusData.status === 'failed') && onJobComplete) {
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
              </p>
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

            <JobFlowSteps
              idPrefix={job.id}
              entry={{
                id: job.id,
                status: details.status,
                progress,
                result: details.result,
              }}
            />
          </div>
        )
      })}
    </div>
  )
}
