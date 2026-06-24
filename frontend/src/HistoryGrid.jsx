import { useCallback, useMemo, useState } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { AllCommunityModule, ModuleRegistry } from 'ag-grid-community'
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-quartz.css'
import DetailModal from './DetailModal'

ModuleRegistry.registerModules([AllCommunityModule])

const formatInt = (value) => {
  if (value === null || value === undefined) return '-'
  return new Intl.NumberFormat().format(Number(value))
}

const normalizeCredits = (usage) => {
  if (!usage) return null
  if (usage.ai_credits_used !== null && usage.ai_credits_used !== undefined) {
    return Number(usage.ai_credits_used)
  }
  if (usage.total_nano_aiu !== null && usage.total_nano_aiu !== undefined) {
    return Number(usage.total_nano_aiu) / 1_000_000_000
  }
  return null
}

const formatCredits = (value) => {
  if (value === null || value === undefined) return '-'
  return Number(value).toFixed(4)
}

const formatCost = (value) => {
  if (value === null || value === undefined) return '-'
  return Number(value).toFixed(4)
}

const formatTokenCompact = (value) => {
  if (value === null || value === undefined) return '-'
  const amount = Number(value)
  if (Number.isNaN(amount)) return '-'
  const abs = Math.abs(amount)
  if (abs >= 1_000_000_000_000) return `${(amount / 1_000_000_000_000).toFixed(2)}T`
  if (abs >= 1_000_000) return `${(amount / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `${(amount / 1_000).toFixed(2)}K`
  return `${Math.round(amount)}`
}

const formatDurationHms = (seconds) => {
  if (seconds === null || seconds === undefined) return '-'
  const total = Math.max(0, Math.round(Number(seconds)))
  if (Number.isNaN(total)) return '-'
  const hrs = Math.floor(total / 3600)
  const mins = Math.floor((total % 3600) / 60)
  const secs = total % 60
  return `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}

export default function HistoryGrid({ history = [], onSelectDetail }) {
  const [selectedModal, setSelectedModal] = useState(null)

  const PRCellRenderer = useCallback((props) => {
    const url = props.data?.result?.pull_request_url
    if (!url) return <span>-</span>
    return (
      <a href={url} target="_blank" rel="noreferrer" style={{ color: '#0b5f8a', textDecoration: 'underline', cursor: 'pointer' }}>
        View PR
      </a>
    )
  }, [])

  const JiraCellRenderer = useCallback((props) => {
    const ticketId = props.data?.request?.jira_ticket_id
    const jiraUrl = props.data?.request?.jira_url
    if (!ticketId || !jiraUrl) return <span>{ticketId || '-'}</span>
    const url = `${jiraUrl.replace(/\/$/, '')}/browse/${ticketId}`
    return (
      <a href={url} target="_blank" rel="noreferrer" style={{ color: '#0b5f8a', textDecoration: 'underline', cursor: 'pointer' }}>
        {ticketId}
      </a>
    )
  }, [])

  const DetailsCellRenderer = useCallback((props) => {
    const label = props.colDef?.headerName || 'Details'
    const entry = props.data
    return (
      <button
        style={{
          background: 'none',
          border: 'none',
          color: '#0b5f8a',
          textDecoration: 'underline',
          cursor: 'pointer',
          padding: 0,
          fontSize: 'inherit',
        }}
        onClick={() => setSelectedModal({ type: props.context.detailType, data: entry })}
      >
        View
      </button>
    )
  }, [])

  const columnDefs = useMemo(() => [
    {
      field: 'request.jira_ticket_id',
      headerName: 'Jira Ticket',
      cellRenderer: JiraCellRenderer,
      filter: true,
      sortable: true,
    },
    {
      field: 'request.repository',
      headerName: 'Repository',
      filter: true,
      sortable: true,
      width: 280,
    },
    {
      field: 'status',
      headerName: 'Status',
      filter: 'agSetColumnFilter',
      sortable: true,
      width: 120,
    },
    {
      field: 'created_at',
      headerName: 'Created',
      sortable: true,
      sort: 'desc',
      valueFormatter: (params) => {
        if (!params.value) return '-'
        return new Date(params.value).toLocaleString()
      },
      width: 180,
    },
    {
      field: 'result.usage.ai_credits_used',
      headerName: 'AI Credits',
      valueGetter: (params) => normalizeCredits(params.data?.result?.usage),
      valueFormatter: (params) => formatCredits(params.value),
      sortable: true,
      width: 120,
    },
    {
      field: 'result.usage.tokens.total',
      headerName: 'Tokens',
      valueFormatter: (params) => formatTokenCompact(params.data?.result?.usage?.tokens?.total),
      sortable: true,
      width: 110,
    },
    {
      field: 'result.usage.ai.duration_seconds',
      headerName: 'Duration',
      valueFormatter: (params) => formatDurationHms(params.data?.result?.usage?.ai?.duration_seconds),
      sortable: true,
      width: 120,
    },
    {
      field: 'result.usage.estimated_cost_usd',
      headerName: 'Cost',
      valueFormatter: (params) => `$${formatCost(params.data?.result?.usage?.estimated_cost_usd)}`,
      sortable: true,
      width: 100,
    },
    {
      headerName: 'PR',
      cellRenderer: PRCellRenderer,
      width: 100,
      filter: false,
      sortable: false,
    },
    {
      headerName: 'Changes',
      cellRenderer: DetailsCellRenderer,
      width: 100,
      context: { detailType: 'changes' },
      filter: false,
      sortable: false,
    },
    {
      headerName: 'Execution',
      cellRenderer: DetailsCellRenderer,
      width: 100,
      context: { detailType: 'execution' },
      filter: false,
      sortable: false,
    },
  ], [JiraCellRenderer, PRCellRenderer, DetailsCellRenderer])

  return (
    <>
      <div style={{ width: '100%', height: '700px', marginTop: '1rem' }}>
        <div className="ag-theme-quartz" style={{ width: '100%', height: '100%' }}>
          <AgGridReact
            rowData={history}
            columnDefs={columnDefs}
            pagination={true}
            paginationPageSize={20}
            paginationPageSizeSelector={[10, 20, 50, 100]}
            defaultColDef={{
              resizable: true,
              filter: true,
              sortable: true,
            }}
            onCellClicked={(event) => {
              if (event.colDef.headerName === 'Jira Ticket' || event.colDef.headerName === 'PR') {
                event.event?.preventDefault()
              }
            }}
          />
        </div>
      </div>
      {selectedModal && <DetailModal modal={selectedModal} onClose={() => setSelectedModal(null)} />}
    </>
  )
}
