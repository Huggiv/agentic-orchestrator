// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import JobFlowSteps, { buildLogRows, computeFlowProgress } from './JobFlowSteps'

describe('JobFlowSteps timeline states', () => {
  it('shows paused state on the current step', () => {
    render(
      <JobFlowSteps
        idPrefix="job"
        entry={{
          status: 'paused',
          current_step: 'prepare_branch',
          progress: [{ name: 'prepare_branch', status: 'running', details: 'development' }],
          result: null,
        }}
      />
    )

    expect(screen.getByText('Paused')).toBeInTheDocument()
  })

  it('preserves blocked_approval and failure details in raw log rows', () => {
    const rows = buildLogRows({
      status: 'blocked_approval',
      progress: [
        { name: 'create_pr', status: 'blocked_approval', details: 'Awaiting approval' },
      ],
      result: null,
      error: null,
    })

    expect(rows[0].status).toBe('blocked_approval')
    expect(rows[0].details).toContain('Awaiting approval')
  })

  it('renders dedicated PR-review flow steps for PR-Review agent entries', () => {
    render(
      <JobFlowSteps
        idPrefix="pr-review"
        entry={{
          status: 'blocked_approval',
          selected_agent: 'PR-Review',
          jira_ticket_id: 'PR-42',
          current_step: 'publish_review_comments',
          progress: [
            { name: 'clone_repository', status: 'success' },
            { name: 'checkout_pull_request', status: 'success' },
            { name: 'agentic_pr_review', status: 'success' },
            { name: 'view_artifacts', status: 'success' },
            { name: 'publish_review_comments', status: 'blocked_approval' },
          ],
          result: null,
        }}
      />
    )

    expect(screen.getByText('Checkout PR')).toBeInTheDocument()
    expect(screen.getByText('Agentic Review')).toBeInTheDocument()
    expect(screen.getByText('View Artifacts')).toBeInTheDocument()
    expect(screen.getByText('Publish Comments')).toBeInTheDocument()
  })

  it('computes PR-review flow progress using dedicated step count', () => {
    const progress = computeFlowProgress({
      selected_agent: 'PR-Review',
      jira_ticket_id: 'PR-101',
      status: 'running',
      progress: [
        { name: 'clone_repository', status: 'success' },
        { name: 'checkout_pull_request', status: 'success' },
        { name: 'agentic_pr_review', status: 'success' },
        { name: 'view_artifacts', status: 'running' },
      ],
      result: null,
    })

    expect(progress.done).toBe(3)
    expect(progress.total).toBe(5)
  })
})
