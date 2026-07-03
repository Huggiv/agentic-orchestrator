// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import ExecutingJobs from './ExecutingJobs'

const makeResponse = (payload, ok = true) => ({
  ok,
  text: async () => JSON.stringify(payload),
})

describe('ExecutingJobs controls', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      if (String(url).includes('/api/orchestrate/job-1')) {
        return makeResponse({
          status: 'running',
          progress: [],
          allowed_actions: ['pause', 'cancel'],
          current_step: 'prepare_branch',
          next_step: 'read_jira',
          result: null,
          error: null,
        })
      }
      if (String(url).includes('/api/orchestrate/job-2')) {
        return makeResponse({
          status: 'blocked_approval',
          progress: [],
          allowed_actions: ['approve', 'reject', 'cancel'],
          current_step: 'create_pr',
          next_step: null,
          result: null,
          error: null,
        })
      }
      return makeResponse({ detail: 'not found' }, false)
    }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('renders pause control when backend allows pause', async () => {
    render(
      <ExecutingJobs
        runningJobs={[{ id: 'job-1', jira_ticket_id: 'AGENT_FLOW-1', repository: 'owner/repo' }]}
      />
    )

    expect(await screen.findByRole('button', { name: 'Pause' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('renders approval actions when backend blocks for checkpoint approval', async () => {
    render(
      <ExecutingJobs
        runningJobs={[{ id: 'job-2', jira_ticket_id: 'AGENT_FLOW-2', repository: 'owner/repo' }]}
      />
    )

    expect(await screen.findByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Reject' })).toBeInTheDocument()
    expect(await screen.findByText(/approval required/i)).toBeInTheDocument()
  })
})
