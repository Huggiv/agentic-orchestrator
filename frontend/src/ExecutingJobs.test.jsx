// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
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

  it('hides interactive controls when allowed_actions is missing or empty', async () => {
    fetch.mockImplementation(async (url) => {
      if (String(url).includes('/api/orchestrate/job-3')) {
        return makeResponse({
          status: 'blocked_approval',
          progress: [],
          current_step: 'create_pr',
          next_step: null,
          result: null,
          error: null,
        })
      }
      return makeResponse({ detail: 'not found' }, false)
    })

    const { container } = render(
      <ExecutingJobs
        runningJobs={[{ id: 'job-3', jira_ticket_id: 'AGENT_FLOW-3', repository: 'owner/repo' }]}
      />
    )

    expect(await screen.findByText(/approval required/i)).toBeInTheDocument()
    expect(within(container).queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument()
    expect(within(container).queryByRole('button', { name: 'Resume' })).not.toBeInTheDocument()
    expect(within(container).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(within(container).queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
    expect(within(container).queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
  })

  it('removes stale action buttons when backend no longer advertises actions', async () => {
    let firstPoll = true
    fetch.mockImplementation(async (url) => {
      if (String(url).includes('/api/orchestrate/job-4')) {
        if (firstPoll) {
          firstPoll = false
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
        return makeResponse({
          status: 'blocked_approval',
          progress: [],
          current_step: 'create_pr',
          next_step: null,
          result: null,
          error: null,
        })
      }
      return makeResponse({ detail: 'not found' }, false)
    })

    const runningJobs = [{ id: 'job-4', jira_ticket_id: 'AGENT_FLOW-4', repository: 'owner/repo' }]
    const { container, rerender } = render(
      <ExecutingJobs
        runningJobs={runningJobs}
        onJobComplete={() => {}}
      />
    )

    expect(await within(container).findByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(within(container).getByRole('button', { name: 'Reject' })).toBeInTheDocument()
    expect(within(container).getByRole('button', { name: 'Cancel' })).toBeInTheDocument()

    rerender(
      <ExecutingJobs
        runningJobs={runningJobs}
        onJobComplete={() => {}}
      />
    )

    await waitFor(() => {
      expect(within(container).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
      expect(within(container).queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
      expect(within(container).queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
    })
  })
})
