// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { retryJob } from './orchestrate'

describe('retryJob', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('posts retry payload and returns parsed response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        text: async () => JSON.stringify({ job_id: 'retry-job-1', status: 'queued' }),
      }))
    )

    const payload = {
      retry_mode: 'from_failed_step',
      start_step: 'push_branch',
      reason: 'recover push',
    }

    const data = await retryJob('job-parent-1', payload)

    expect(fetch).toHaveBeenCalledWith('/api/orchestrate/job-parent-1/retry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    expect(data.job_id).toBe('retry-job-1')
  })

  it('throws backend error detail for failed retry request', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        text: async () => JSON.stringify({ detail: 'Retries are only allowed for failed jobs' }),
      }))
    )

    await expect(retryJob('job-parent-2', { retry_mode: 'failed_step_only' })).rejects.toThrow(
      'Retries are only allowed for failed jobs'
    )
  })
})
