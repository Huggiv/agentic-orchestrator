// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

  it('falls back to approval controls when blocked at create_pr and allowed_actions is missing', async () => {
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
    expect(await within(container).findByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(within(container).getByRole('button', { name: 'Reject' })).toBeInTheDocument()
    expect(within(container).getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('shows artifact view/download actions and calls handlers', async () => {
    const onArtifactOpen = vi.fn()
    const onArtifactDownload = vi.fn()

    fetch.mockImplementation(async (url) => {
      if (String(url).includes('/api/orchestrate/job-artifacts')) {
        return makeResponse({
          status: 'blocked_approval',
          progress: [],
          allowed_actions: ['approve', 'reject', 'cancel'],
          current_step: 'publish_review_comments',
          next_step: null,
          result: {
            artifacts: [
              { path: '.agent_flow_agentic/pr-42-agentical-flow.md', content: '# flow' },
            ],
          },
          error: null,
        })
      }
      return makeResponse({ detail: 'not found' }, false)
    })

    render(
      <ExecutingJobs
        runningJobs={[{ id: 'job-artifacts', jira_ticket_id: 'PR-42', repository: 'owner/repo', selected_agent: 'PR-Review' }]}
        onArtifactOpen={onArtifactOpen}
        onArtifactDownload={onArtifactDownload}
      />
    )

    expect(await screen.findByRole('button', { name: '.agent_flow_agentic/pr-42-agentical-flow.md' })).toBeInTheDocument()
    const downloadBtn = await screen.findByRole('button', { name: 'Download .agent_flow_agentic/pr-42-agentical-flow.md' })
    expect(downloadBtn).toBeInTheDocument()

    await (await screen.findByRole('button', { name: '.agent_flow_agentic/pr-42-agentical-flow.md' })).click()
    expect(onArtifactOpen).toHaveBeenCalledTimes(1)

    await downloadBtn.click()
    expect(onArtifactDownload).toHaveBeenCalledTimes(1)
  })

  it('disables artifact download when content is empty', async () => {
    const onArtifactDownload = vi.fn()

    fetch.mockImplementation(async (url) => {
      if (String(url).includes('/api/orchestrate/job-empty-artifact')) {
        return makeResponse({
          status: 'running',
          progress: [],
          allowed_actions: ['cancel'],
          current_step: 'view_artifacts',
          next_step: null,
          result: {
            artifacts: [
              { path: '.agent_flow_agentic/empty.md', content: '' },
            ],
          },
          error: null,
        })
      }
      return makeResponse({ detail: 'not found' }, false)
    })

    render(
      <ExecutingJobs
        runningJobs={[{ id: 'job-empty-artifact', jira_ticket_id: 'PR-77', repository: 'owner/repo', selected_agent: 'PR-Review' }]}
        onArtifactDownload={onArtifactDownload}
      />
    )

    const disabledDownload = await screen.findByRole('button', { name: 'Download .agent_flow_agentic/empty.md' })
    expect(disabledDownload).toBeDisabled()

    fireEvent.click(disabledDownload)
    expect(onArtifactDownload).not.toHaveBeenCalled()
  })

  it('removes approval action buttons when backend exits approval-blocked state', async () => {
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
          status: 'running',
          progress: [],
          current_step: 'push_branch',
          next_step: 'create_pr',
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
