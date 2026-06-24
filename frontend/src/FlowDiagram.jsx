const STATUS_COLOR = {
  idle: '#cad7e2',
  running: '#0b5f8a',
  success: '#1f8f5f',
  skipped: '#f2a93b',
  failed: '#dc3545',
  installing: '#6f42c1',
}

const STATUS_SURFACE = {
  idle: '#f7fafc',
  running: '#e8f4fb',
  success: '#e7f7ef',
  skipped: '#fff4de',
  failed: '#fdecee',
  installing: '#f1ebfb',
}

const STATUS_LABEL = {
  idle: 'Idle',
  running: 'Running',
  success: 'Done',
  skipped: 'Skipped',
  failed: 'Failed',
  installing: 'Init',
}

export function StageIcon({ stepKey, color }) {
  const common = {
    fill: 'none',
    stroke: color,
    strokeWidth: 1.8,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
  }

  switch (stepKey) {
    case 'clone_repository':
      return (
        <>
          <path {...common} d="M7 10h4l2 2h8v8H7z" />
          <path {...common} d="M14 7v7" />
          <path {...common} d="M11.5 11 14 14l2.5-3" />
        </>
      )
    case 'check_gh_cli':
      return (
        <>
          <rect {...common} x="5" y="7" width="18" height="12" rx="2.5" />
          <path {...common} d="m9 11 2.5 2-2.5 2" />
          <path {...common} d="M14.5 15h4.5" />
        </>
      )
    case 'github_auth':
      return (
        <>
          <path {...common} d="M14 5 8 7v5c0 4 2.6 6.7 6 8 3.4-1.3 6-4 6-8V7z" />
          <path {...common} d="m11.5 13 1.8 1.8 3.2-3.6" />
        </>
      )
    case 'check_copilot_cli':
      return (
        <>
          <path {...common} d="M10 17c0 2.2 1.8 4 4 4s4-1.8 4-4v-1.5a4 4 0 1 0-8 0z" />
          <path {...common} d="M10.5 10.5c.7-2 2-3 3.5-3s2.8 1 3.5 3" />
          <circle cx="12" cy="15" r="0.9" fill={color} />
          <circle cx="16" cy="15" r="0.9" fill={color} />
          <path {...common} d="M13 18h2" />
        </>
      )
    case 'auth_setup':
      return (
        <>
          <path {...common} d="M14 5 8 7v5c0 4 2.6 6.7 6 8 3.4-1.3 6-4 6-8V7z" />
          <path {...common} d="M8 14h12" />
          <path {...common} d="m11.5 13 1.8 1.8 3.2-3.6" />
          <circle cx="11" cy="10" r="0.9" fill={color} />
          <circle cx="17" cy="10" r="0.9" fill={color} />
        </>
      )
    case 'prepare_branch':
      return (
        <>
          <circle {...common} cx="9" cy="8" r="2.5" />
          <circle {...common} cx="19" cy="11" r="2.5" />
          <circle {...common} cx="19" cy="19" r="2.5" />
          <path {...common} d="M11.5 8h3a4.5 4.5 0 0 1 4.5 4.5V16" />
        </>
      )
    case 'read_jira':
      return (
        <>
          <rect {...common} x="7" y="6" width="12" height="16" rx="2" />
          <path {...common} d="M10 10h6" />
          <path {...common} d="M10 14h6" />
          <path {...common} d="M10 18h4" />
        </>
      )
    case 'agentic_implementation':
      return (
        <>
          <path {...common} d="m8 16-2 2 2 2" />
          <path {...common} d="m20 16 2 2-2 2" />
          <path {...common} d="M14 10 12 22" />
          <path {...common} d="m16 6 1 3 3 1-3 1-1 3-1-3-3-1 3-1z" />
        </>
      )
    case 'commit_changes':
      return (
        <>
          <path {...common} d="M8 6h8l4 4v10a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z" />
          <path {...common} d="M16 6v4h4" />
          <path {...common} d="m10.5 16 2 2 4-4" />
        </>
      )
    case 'push_branch':
      return (
        <>
          <path {...common} d="M14 20V9" />
          <path {...common} d="m10 13 4-4 4 4" />
          <path {...common} d="M8 20h12" />
        </>
      )
    case 'create_pr':
      return (
        <>
          <circle {...common} cx="9" cy="8" r="2.3" />
          <circle {...common} cx="19" cy="8" r="2.3" />
          <circle {...common} cx="19" cy="19" r="2.3" />
          <path {...common} d="M11.3 8h4.8" />
          <path {...common} d="M19 10.3v6.4" />
          <path {...common} d="m13.5 16 2.5 2.5 4-4.5" />
        </>
      )
    default:
      return <circle cx="14" cy="14" r="5" fill={color} opacity="0.4" />
  }
}

function resolveStepStatus(key, progress, jobStatus) {
  // Walk progress events for this step key
  const events = progress.filter((e) => e.name === key)
  if (events.length === 0) return 'idle'

  const last = events[events.length - 1]
  if (last.status === 'success') return 'success'
  if (last.status === 'skipped') return 'skipped'
  if (last.status === 'failed')  return 'failed'
  if (last.status === 'installing') return 'installing'
  return 'running'
}

export default function FlowDiagram({ steps, progress, jobStatus }) {
  const NODE_W = 132
  const NODE_H = 92
  const H_GAP = 38
  const V_GAP = 78
  const COLS = 5
  const ROWS = Math.ceil(steps.length / COLS)
  const SVG_W = COLS * NODE_W + (COLS - 1) * H_GAP + 40
  const SVG_H = ROWS * NODE_H + (ROWS - 1) * V_GAP + 40

  // Map step index → row/col grid position
  const pos = (idx) => {
    const row = Math.floor(idx / COLS)
    const col = idx % COLS
    // Snake layout: odd rows go right-to-left
    const actualCol = row % 2 === 0 ? col : COLS - 1 - col
    return {
      x: 16 + actualCol * (NODE_W + H_GAP),
      y: 20 + row * (NODE_H + V_GAP),
    }
  }

  const statuses = steps.map((s) => resolveStepStatus(s.key, progress, jobStatus))

  // Build connector paths between consecutive nodes
  const connectors = steps.slice(0, -1).map((_, idx) => {
    const a = pos(idx)
    const b = pos(idx + 1)
    const aRow = Math.floor(idx / COLS)
    const bRow = Math.floor((idx + 1) / COLS)
    const color = statuses[idx] === 'success' ? STATUS_COLOR.success : STATUS_COLOR.idle
    const strokeW = 3

    if (aRow === bRow) {
      // Horizontal connector
      const x1 = a.x + NODE_W
      const y1 = a.y + NODE_H / 2
      const x2 = b.x
      const y2 = b.y + NODE_H / 2
      return (
        <g key={`conn-${idx}`}>
          <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={strokeW} />
          <polygon
            points={`${x2},${y2} ${x2 - 7},${y2 - 5} ${x2 - 7},${y2 + 5}`}
            fill={color}
          />
        </g>
      )
    }

    // Vertical wrap connector (end of row to start of next row)
    const fromRight = aRow % 2 === 0
    const ax = fromRight ? a.x + NODE_W : a.x
    const ay = a.y + NODE_H / 2
    const bxEnd = fromRight ? b.x + NODE_W : b.x
    const byEnd = b.y + NODE_H / 2
    const midY = ay + V_GAP / 2

    return (
      <g key={`conn-${idx}`}>
        <polyline
          points={`${ax},${ay} ${ax + (fromRight ? 12 : -12)},${ay} ${ax + (fromRight ? 12 : -12)},${midY} ${bxEnd + (fromRight ? 0 : 0)},${midY} ${bxEnd},${byEnd}`}
          fill="none"
          stroke={color}
          strokeWidth={strokeW}
        />
        <polygon
            points={`${bxEnd},${byEnd} ${bxEnd + (fromRight ? -7 : 7)},${byEnd - 5} ${bxEnd + (fromRight ? -7 : 7)},${byEnd + 5}`}
          fill={color}
        />
      </g>
    )
  })

  const nodes = steps.map((step, idx) => {
    const { x, y } = pos(idx)
    const status = statuses[idx]
    const fill = STATUS_COLOR[status] ?? STATUS_COLOR.idle
    const surface = STATUS_SURFACE[status] ?? STATUS_SURFACE.idle
    const isActive = status === 'running' || status === 'installing'
    const iconColor = status === 'idle' ? '#6f8799' : fill

    return (
      <g key={step.key} className={isActive ? 'flow-node-active' : ''}>
        {isActive && (
          <rect
            x={x - 3} y={y - 3}
            width={NODE_W + 6} height={NODE_H + 6}
            rx={20} ry={20}
            fill="none"
            stroke={fill}
            strokeWidth={2}
            opacity={0.4}
            className="flow-pulse"
          />
        )}
        <rect
          x={x} y={y}
          width={NODE_W} height={NODE_H}
          rx={18} ry={18}
          fill={surface}
          stroke={fill}
          strokeWidth={status === 'idle' ? 1.5 : 2.5}
        />
        <circle cx={x + 22} cy={y + 22} r={16} fill="white" stroke={fill} strokeWidth={1.5} />
        <g transform={`translate(${x + 8}, ${y + 8})`}>
          <StageIcon stepKey={step.key} color={iconColor} />
        </g>
        <text
          x={x + 22}
          y={y + 54}
          textAnchor="start"
          fontSize={11}
          fill="#183142"
          fontWeight="600"
          letterSpacing="0.02em"
        >
          {step.label}
        </text>
        <rect x={x + 16} y={y + 62} width={50} height={18} rx={9} fill={fill} opacity={status === 'idle' ? 0.18 : 0.18} />
        <text x={x + 41} y={y + 74.5} textAnchor="middle" fontSize={9.5} fill={fill} fontWeight="700">
          {STATUS_LABEL[status]}
        </text>
      </g>
    )
  })

  const overallLabel = {
    idle:    'Waiting',
    queued:  'Queued',
    running: 'Running',
    success: 'Completed',
    failed:  'Failed',
  }[jobStatus] ?? jobStatus

  const overallColor = {
    idle:    '#6a8aa2',
    queued:  '#0b5f8a',
    running: '#0b5f8a',
    success: '#28a745',
    failed:  '#dc3545',
  }[jobStatus] ?? '#6a8aa2'

  return (
    <div className="flow-diagram-wrapper">
      <div className="flow-diagram-header">
        <span className="flow-status-label" style={{ color: overallColor }}>
          {overallLabel}
        </span>
        <span className="flow-step-count">
          {statuses.filter((s) => s === 'success' || s === 'skipped').length} / {steps.length} steps done
        </span>
      </div>
      <div className="flow-diagram-scroll">
        <svg
          viewBox={`0 0 ${SVG_W} ${SVG_H}`}
          width={SVG_W}
          height={SVG_H}
          style={{ display: 'block', maxWidth: '100%' }}
        >
          <defs>
            <style>{`
              @keyframes flow-pulse {
                0%   { opacity: 0.65; }
                50%  { opacity: 0.2; }
                100% { opacity: 0.65; }
              }
              .flow-pulse {
                animation: flow-pulse 1.2s ease-in-out infinite;
              }
            `}</style>
          </defs>
          {connectors}
          {nodes}
        </svg>
      </div>
      {/* Mini legend */}
      <div className="flow-legend">
        {Object.entries(STATUS_COLOR).map(([s, c]) => (
          <span key={s} className="flow-legend-item">
            <svg width={10} height={10}>
              <circle cx={5} cy={5} r={4} fill={c} />
            </svg>
            {s}
          </span>
        ))}
      </div>
    </div>
  )
}
