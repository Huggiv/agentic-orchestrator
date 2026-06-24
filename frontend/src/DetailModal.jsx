import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const formatInt = (value) => {
  if (value === null || value === undefined) return '-'
  return new Intl.NumberFormat().format(Number(value))
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

export default function DetailModal({ modal, onClose }) {
  if (!modal) return null

  const { type, data } = modal

  const renderChanges = () => (
    <div style={{ padding: '1rem' }}>
      <h3>Code Changes</h3>
      <p>
        <strong>Added:</strong> {formatInt(data?.result?.usage?.changes?.added)} lines
      </p>
      <p>
        <strong>Removed:</strong> {formatInt(data?.result?.usage?.changes?.removed)} lines
      </p>
    </div>
  )

  const renderExecution = () => (
    <div style={{ padding: '1rem' }}>
      <h3>Execution Steps</h3>
      <ul style={{ maxHeight: '400px', overflowY: 'auto' }}>
        {(data?.result?.steps || []).map((step, idx) => (
          <li key={idx}>
            <strong>{step.name}</strong> - {step.status}
            {step.details ? ` (${step.details})` : ''}
          </li>
        ))}
      </ul>
    </div>
  )

  const renderUsage = () => (
    <div style={{ padding: '1rem' }}>
      <h3>Usage Details</h3>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <tbody>
          <tr style={{ borderBottom: '1px solid #ddd' }}>
            <td style={{ padding: '0.5rem' }}><strong>AI Credits:</strong></td>
            <td style={{ padding: '0.5rem' }}>{data?.result?.usage?.ai_credits_used || '-'}</td>
          </tr>
          <tr style={{ borderBottom: '1px solid #ddd' }}>
            <td style={{ padding: '0.5rem' }}><strong>Tokens Input:</strong></td>
            <td style={{ padding: '0.5rem' }}>{formatTokenCompact(data?.result?.usage?.tokens?.input)}</td>
          </tr>
          <tr style={{ borderBottom: '1px solid #ddd' }}>
            <td style={{ padding: '0.5rem' }}><strong>Tokens Output:</strong></td>
            <td style={{ padding: '0.5rem' }}>{formatTokenCompact(data?.result?.usage?.tokens?.output)}</td>
          </tr>
          <tr style={{ borderBottom: '1px solid #ddd' }}>
            <td style={{ padding: '0.5rem' }}><strong>Tokens Cached:</strong></td>
            <td style={{ padding: '0.5rem' }}>{formatTokenCompact(data?.result?.usage?.tokens?.cached)}</td>
          </tr>
          <tr style={{ borderBottom: '1px solid #ddd' }}>
            <td style={{ padding: '0.5rem' }}><strong>Duration:</strong></td>
            <td style={{ padding: '0.5rem' }}>{data?.result?.usage?.ai?.duration_text || '-'}</td>
          </tr>
          <tr>
            <td style={{ padding: '0.5rem' }}><strong>Cost:</strong></td>
            <td style={{ padding: '0.5rem' }}>${data?.result?.usage?.estimated_cost_usd || '-'}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )

  const renderJiraDetails = () => (
    <div style={{ padding: '1rem' }}>
      <h3>Jira Issue Details</h3>
      <p>
        <strong>Ticket:</strong> {data?.request?.jira_ticket_id}
      </p>
      <p>
        <strong>Summary:</strong> {data?.request?.jira_summary || '-'}
      </p>
      <div style={{ marginTop: '1rem' }}>
        <strong>Description:</strong>
        <div style={{ background: '#f5f5f5', padding: '0.5rem', borderRadius: '4px', marginTop: '0.5rem', maxHeight: '300px', overflowY: 'auto' }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {data?.request?.jira_description || 'No description'}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  )

  const getTitleByType = () => {
    switch (type) {
      case 'changes':
        return 'Code Changes'
      case 'execution':
        return 'Execution Steps'
      case 'usage':
        return 'Usage Details'
      case 'jira':
        return 'Jira Details'
      default:
        return 'Details'
    }
  }

  const getContent = () => {
    switch (type) {
      case 'changes':
        return renderChanges()
      case 'execution':
        return renderExecution()
      case 'usage':
        return renderUsage()
      case 'jira':
        return renderJiraDetails()
      default:
        return <div>Unknown detail type</div>
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(19, 33, 47, 0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 50,
        padding: '1rem',
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: 'min(700px, 100%)',
          maxHeight: '85vh',
          overflow: 'auto',
          background: '#ffffff',
          borderRadius: '16px',
          border: '1px solid #d9e8f2',
          boxShadow: '0 20px 50px rgba(11, 23, 38, 0.3)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '0.75rem',
            padding: '0.8rem 1rem',
            borderBottom: '1px solid #dbe8f2',
          }}
        >
          <h3 style={{ margin: 0, fontSize: '0.95rem', color: '#1f4156' }}>{getTitleByType()}</h3>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              fontSize: '1.5rem',
              cursor: 'pointer',
              color: '#6a8aa2',
            }}
          >
            ✕
          </button>
        </div>
        <div style={{ fontSize: '0.83rem', lineHeight: 1.45, color: '#1f3647', background: '#fcfdff' }}>
          {getContent()}
        </div>
      </div>
    </div>
  )
}
