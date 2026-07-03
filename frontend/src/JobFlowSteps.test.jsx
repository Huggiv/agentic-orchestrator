// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import JobFlowSteps, { buildLogRows } from './JobFlowSteps'

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
})
