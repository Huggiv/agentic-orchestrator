import { useEffect, useMemo, useRef, useState } from 'react'

const CHAT_STORAGE_KEY = 'agentflow.chat.sessions.v1'
const CHAT_LAUNCHER_POS_KEY = 'agentflow.chat.launcher.position.v1'
const MAX_CHAT_SESSIONS = 5

const createInitialAssistantMessage = () => ({
  id: `assistant-initial-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  role: 'assistant',
  kind: 'text',
  createdAt: Date.now(),
  content:
    'I can run one or more Jira-driven workflows from chat. Include ticket keys like AGENT_FLOW-101 and your grooming guidance in one prompt.',
})

const createSession = () => ({
  id: `session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  title: 'New Chat',
  updatedAt: Date.now(),
  messages: [createInitialAssistantMessage()],
})

const parseApiPayload = (raw) => {
  if (!raw) return {}
  try {
    return JSON.parse(raw)
  } catch {
    return { detail: raw }
  }
}

export default function ChatConsole({
  repository,
  setRepository,
  reviewer,
  setReviewer,
  selectedAgent,
  setSelectedAgent,
  selectedModel,
  setSelectedModel,
  availableAgents,
  availableModels,
  modelsLoading,
  onRefreshModels,
  onJobsQueued,
}) {
  const [isOpen, setIsOpen] = useState(false)
  const [launcherPosition, setLauncherPosition] = useState(null)
  const dragRef = useRef({ dragging: false, offsetX: 0, offsetY: 0 })
  const [sessions, setSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState('')
  const [draft, setDraft] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [isConfirming, setIsConfirming] = useState(false)
  const [chatError, setChatError] = useState('')
  const [streamStatus, setStreamStatus] = useState('')
  const [streamPhases, setStreamPhases] = useState([])
  const listRef = useRef(null)

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) || null,
    [sessions, activeSessionId]
  )

  const messages = activeSession?.messages || []

  const orderedMessages = useMemo(
    () => [...messages].sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0)),
    [messages]
  )

  const canSend = useMemo(() => {
    return Boolean(draft.trim()) && Boolean(repository.trim()) && !isSending && !isConfirming && Boolean(activeSessionId)
  }, [draft, repository, isSending, isConfirming, activeSessionId])

  const persistSessions = (nextSessions, nextActiveSessionId) => {
    const limited = [...nextSessions]
      .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
      .slice(0, MAX_CHAT_SESSIONS)
    setSessions(limited)

    const effectiveActiveId = limited.some((session) => session.id === nextActiveSessionId)
      ? nextActiveSessionId
      : (limited[0]?.id || '')
    setActiveSessionId(effectiveActiveId)

    try {
      localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(limited))
    } catch {
      return
    }
  }

  const updateActiveSession = (updater) => {
    setSessions((prev) => {
      const next = prev.map((session) => {
        if (session.id !== activeSessionId) return session
        const updated = updater(session)
        const firstUserMessage = updated.messages.find((item) => item.role === 'user')
        return {
          ...updated,
          updatedAt: Date.now(),
          title: firstUserMessage ? firstUserMessage.content.slice(0, 40) : (updated.title || 'New Chat'),
        }
      })
      try {
        localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(next.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0)).slice(0, MAX_CHAT_SESSIONS)))
      } catch {
        return next
      }
      return next
    })
  }

  const createNewSession = () => {
    const session = createSession()
    persistSessions([session, ...sessions], session.id)
    setDraft('')
    setStreamStatus('')
    setStreamPhases([])
    setChatError('')
  }

  const deleteSession = (sessionId) => {
    const next = sessions.filter((session) => session.id !== sessionId)
    if (next.length === 0) {
      const replacement = createSession()
      persistSessions([replacement], replacement.id)
      return
    }
    const nextActive = sessionId === activeSessionId ? next[0].id : activeSessionId
    persistSessions(next, nextActive)
  }

  useEffect(() => {
    try {
      const raw = localStorage.getItem(CHAT_STORAGE_KEY)
      if (!raw) {
        const fresh = createSession()
        setSessions([fresh])
        setActiveSessionId(fresh.id)
        return
      }
      const parsed = JSON.parse(raw)
      const loaded = Array.isArray(parsed) ? parsed.slice(0, MAX_CHAT_SESSIONS) : []
      if (loaded.length === 0) {
        const fresh = createSession()
        setSessions([fresh])
        setActiveSessionId(fresh.id)
        return
      }
      setSessions(loaded)
      setActiveSessionId(loaded[0].id)
    } catch {
      const fresh = createSession()
      setSessions([fresh])
      setActiveSessionId(fresh.id)
    }
  }, [])

  useEffect(() => {
    try {
      const raw = localStorage.getItem(CHAT_LAUNCHER_POS_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw)
      if (typeof parsed?.x === 'number' && typeof parsed?.y === 'number') {
        setLauncherPosition({ x: parsed.x, y: parsed.y })
      }
    } catch {
      return
    }
  }, [])

  const persistLauncherPosition = (position) => {
    setLauncherPosition(position)
    try {
      localStorage.setItem(CHAT_LAUNCHER_POS_KEY, JSON.stringify(position))
    } catch {
      return
    }
  }

  const bindDragHandlers = () => {
    const onPointerMove = (event) => {
      if (!dragRef.current.dragging) return
      const x = event.clientX - dragRef.current.offsetX
      const y = event.clientY - dragRef.current.offsetY
      const maxX = Math.max(0, window.innerWidth - 60)
      const maxY = Math.max(0, window.innerHeight - 60)
      persistLauncherPosition({
        x: Math.min(maxX, Math.max(0, x)),
        y: Math.min(maxY, Math.max(0, y)),
      })
    }

    const onPointerUp = () => {
      dragRef.current.dragging = false
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
    }

    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
  }

  const startLauncherDrag = (event) => {
    const rect = event.currentTarget.getBoundingClientRect()
    dragRef.current.dragging = true
    dragRef.current.offsetX = event.clientX - rect.left
    dragRef.current.offsetY = event.clientY - rect.top
    bindDragHandlers()
  }

  useEffect(() => {
    if (!listRef.current) return
    listRef.current.scrollTop = listRef.current.scrollHeight
  }, [orderedMessages, streamStatus])

  const appendAssistantDelta = (assistantId, delta) => {
    if (!delta) return
    updateActiveSession((session) => ({
      ...session,
      messages: session.messages.map((message) =>
        message.id === assistantId
          ? { ...message, content: `${message.content || ''}${delta}` }
          : message
      ),
    }))
  }

  const setAssistantText = (assistantId, text) => {
    updateActiveSession((session) => ({
      ...session,
      messages: session.messages.map((message) =>
        message.id === assistantId
          ? { ...message, content: text || '' }
          : message
      ),
    }))
  }

  const addPhase = (label) => {
    if (!label) return
    setStreamPhases((prev) => {
      if (prev.length > 0 && prev[prev.length - 1].label === label) {
        return prev
      }
      return [
        ...prev,
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          label,
        },
      ]
    })
  }

  const processStreamingResponse = async (response, assistantId) => {
    if (!response.body) {
      throw new Error('Streaming not supported in this browser')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let finalResult = null

    const handleEvent = (eventName, eventData) => {
      if (eventName === 'status') {
        const label = eventData?.message || ''
        setStreamStatus(label)
        addPhase(label)
      }
      if (eventName === 'tickets') {
        const tickets = Array.isArray(eventData?.tickets) ? eventData.tickets : []
        const label = tickets.length > 0 ? `Found ${tickets.length} ticket(s): ${tickets.join(', ')}` : 'No Jira ticket found in prompt'
        setStreamStatus(label)
        addPhase(label)
      }
      if (eventName === 'queued_job') {
        const label = `Queued workflow for ${eventData?.jira_ticket_id || 'ticket'}`
        setStreamStatus(label)
        addPhase(label)
      }
      if (eventName === 'ticket_failed') {
        const label = `Skipped ${eventData?.jira_ticket_id || 'ticket'} due to Jira lookup error`
        setStreamStatus(label)
        addPhase(label)
      }
      if (eventName === 'assistant_token') {
        appendAssistantDelta(assistantId, eventData?.delta || '')
      }
      if (eventName === 'result') {
        finalResult = eventData
        addPhase('Completed response generation')
      }
      if (eventName === 'done') {
        setStreamStatus('')
      }
    }

    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''

      for (const block of parts) {
        const lines = block.split('\n')
        let eventName = 'message'
        const dataLines = []
        for (const line of lines) {
          if (line.startsWith('event:')) {
            eventName = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim())
          }
        }
        if (dataLines.length === 0) continue

        let payload = {}
        try {
          payload = JSON.parse(dataLines.join('\n'))
        } catch {
          payload = {}
        }
        handleEvent(eventName, payload)
      }
    }

    return finalResult
  }

  const sendMessage = async (event) => {
    event.preventDefault()
    if (!canSend) return

    const userMessage = draft.trim()
    setDraft('')
    setChatError('')
    setStreamStatus('Connecting...')
    setStreamPhases([{ id: `phase-${Date.now()}`, label: 'Connecting...' }])
    setIsSending(true)

    const userId = `user-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const assistantId = `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

    updateActiveSession((session) => ({
      ...session,
      messages: [
        ...session.messages,
        { id: userId, role: 'user', kind: 'text', createdAt: Date.now(), content: userMessage },
        { id: assistantId, role: 'assistant', kind: 'text', createdAt: Date.now(), content: '' },
      ],
    }))

    try {
      const response = await fetch('/api/chat/message/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userMessage,
          repository,
          base_branch: 'development',
          reviewer: reviewer || null,
          selected_agent: selectedAgent,
          selected_model: selectedModel || null,
        }),
      })

      if (!response.ok) {
        const raw = await response.text()
        const data = parseApiPayload(raw)
        throw new Error(data.detail || 'Chat request failed')
      }

      const data = await processStreamingResponse(response, assistantId)
      const queuedJobs = Array.isArray(data?.queued_jobs) ? data.queued_jobs : []
      if (queuedJobs.length > 0) {
        onJobsQueued(queuedJobs)
      }

      if (data?.requires_confirmation && data?.plan_id) {
        const confirmId = `confirm-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
        updateActiveSession((session) => ({
          ...session,
          messages: [
            ...session.messages,
            {
              id: confirmId,
              role: 'assistant',
              kind: 'confirmation',
              createdAt: Date.now(),
              resolved: false,
              content: 'Review this groomed issue and confirm workflow trigger.',
              planId: data.plan_id,
              tickets: Array.isArray(data.tickets) ? data.tickets : [],
              groomedIssue: data.groomed_issue || '',
            },
          ],
        }))
      }

      if ((data?.assistant_message || '').trim()) {
        setAssistantText(assistantId, data.assistant_message)
      } else {
        setAssistantText(assistantId, 'Done.')
      }
    } catch (err) {
      setChatError(err.message)
      addPhase('Request failed')
      setStreamStatus('')
      updateActiveSession((session) => {
        const next = [...session.messages]
        const idx = next.findIndex((message) => message.id === assistantId)
        if (idx >= 0) {
          next[idx] = {
            ...next[idx],
            content: 'I could not process that prompt. Please verify ticket keys and try again.',
          }
          return { ...session, messages: next }
        }
        next.push({
          id: `assistant-error-${Date.now()}`,
          role: 'assistant',
          kind: 'text',
          createdAt: Date.now(),
          content: 'I could not process that prompt. Please verify ticket keys and try again.',
        })
        return { ...session, messages: next }
      })
    } finally {
      setStreamStatus('')
      setIsSending(false)
    }
  }

  const confirmPlan = async (planId, confirm, messageId) => {
    if (!planId) return

    setIsConfirming(true)
    setChatError('')

    const assistantId = `assistant-confirm-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const userDecisionId = `user-decision-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

    updateActiveSession((session) => ({
      ...session,
      messages: [
        ...session.messages.map((message) =>
          message.id === messageId ? { ...message, resolved: true } : message
        ),
        {
          id: userDecisionId,
          role: 'user',
          kind: 'text',
          createdAt: Date.now(),
          content: confirm ? 'Confirm and trigger workflow' : 'Cancel this workflow request',
        },
        {
          id: assistantId,
          role: 'assistant',
          kind: 'text',
          createdAt: Date.now(),
          content: '',
        },
      ],
    }))

    try {
      const response = await fetch('/api/chat/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: planId, confirm }),
      })
      const raw = await response.text()
      const data = parseApiPayload(raw)
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to confirm grooming plan')
      }

      if (confirm) {
        const queuedJobs = Array.isArray(data.queued_jobs) ? data.queued_jobs : []
        if (queuedJobs.length > 0) {
          onJobsQueued(queuedJobs)
          updateActiveSession((session) => ({
            ...session,
            messages: [
              ...session.messages,
              {
                id: `job-controls-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                role: 'assistant',
                kind: 'job_controls',
                createdAt: Date.now(),
                content: 'Workflow jobs started. You can cancel any job below.',
                jobs: queuedJobs.map((job) => ({
                  ...job,
                  status: 'queued',
                })),
              },
            ],
          }))
        }
      }

      setAssistantText(assistantId, data.assistant_message || (confirm ? 'Confirmed.' : 'Cancelled.'))
    } catch (err) {
      setChatError(err.message)
      setAssistantText(assistantId, 'I could not process your confirmation right now.')
    } finally {
      setIsConfirming(false)
    }
  }

  const cancelChatJob = async (messageId, jobId) => {
    updateActiveSession((session) => ({
      ...session,
      messages: session.messages.map((message) => {
        if (message.id !== messageId || message.kind !== 'job_controls') return message
        return {
          ...message,
          jobs: (message.jobs || []).map((job) =>
            job.job_id === jobId ? { ...job, status: 'cancelling' } : job
          ),
        }
      }),
    }))

    try {
      const response = await fetch(`/api/chat/cancel/${jobId}`, { method: 'POST' })
      const raw = await response.text()
      const data = parseApiPayload(raw)
      if (!response.ok) throw new Error(data.detail || 'Failed to cancel job')

      updateActiveSession((session) => ({
        ...session,
        messages: session.messages.map((message) => {
          if (message.id !== messageId || message.kind !== 'job_controls') return message
          return {
            ...message,
            jobs: (message.jobs || []).map((job) =>
              job.job_id === jobId ? { ...job, status: data.status || 'cancelled' } : job
            ),
          }
        }),
      }))
    } catch (err) {
      setChatError(err.message)
      updateActiveSession((session) => ({
        ...session,
        messages: session.messages.map((message) => {
          if (message.id !== messageId || message.kind !== 'job_controls') return message
          return {
            ...message,
            jobs: (message.jobs || []).map((job) =>
              job.job_id === jobId ? { ...job, status: 'error' } : job
            ),
          }
        }),
      }))
    }
  }

  if (!isOpen) {
    return (
      <div
        className="chat-fab-shell chat-fab-shell--launcher"
        style={launcherPosition ? { left: `${launcherPosition.x}px`, top: `${launcherPosition.y}px`, right: 'auto', bottom: 'auto', transform: 'none' } : undefined}
      >
        <button
          type="button"
          className="chat-fab-launcher"
          onPointerDown={startLauncherDrag}
          onDoubleClick={() => setIsOpen(true)}
          title="Drag to move. Double-click to open."
        >
          🤖
        </button>
      </div>
    )
  }

  return (
    <div className="chat-fab-shell">
      <section className="panel chat-panel chat-panel--floating">
        <div className="chat-header">
          <strong>Copilot Chat</strong>
          <div className="chat-header-actions">
            <button type="button" onClick={createNewSession}>New</button>
            <button type="button" onClick={() => setIsOpen(false)}>Minimize</button>
          </div>
        </div>

        <div className="chat-settings chat-settings--top">
          <label>
            Repository
            <input value={repository} onChange={(e) => setRepository(e.target.value)} placeholder="owner/repo" required />
          </label>

          <label>
            Reviewer
            <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="teammate-name" />
          </label>
        </div>

        <div className="chat-layout">
          <aside className="chat-sessions">
            <h4>Recent Chats</h4>
            {sessions.map((session) => (
              <div key={session.id} className={`chat-session-row${session.id === activeSessionId ? ' chat-session-row--active' : ''}`}>
                <button
                  type="button"
                  className="chat-session-item"
                  onClick={() => setActiveSessionId(session.id)}
                >
                  <span>{session.title || 'Chat'}</span>
                </button>
                <button
                  type="button"
                  className="chat-session-delete"
                  onClick={() => deleteSession(session.id)}
                  title="Delete chat"
                >
                  x
                 </button>
               </div>
             ))}
           </aside>

          <div className="chat-main">
            <div className="chat-thread" ref={listRef}>
              {orderedMessages.map((message) => (
                <article
                  key={message.id}
                  className={`chat-message chat-message--${message.role}`}
                >
                  <header>{message.role === 'assistant' ? 'Copilot' : 'You'}</header>
                  <p>{message.content}</p>

                  {message.kind === 'confirmation' && !message.resolved && (
                    <div className="chat-inline-confirm">
                      <p><strong>Tickets:</strong> {(message.tickets || []).join(', ')}</p>
                      <pre>{message.groomedIssue || 'No groomed issue text available.'}</pre>
                      <div className="chat-confirm-actions">
                        <button
                          type="button"
                          onClick={() => confirmPlan(message.planId, true, message.id)}
                          disabled={isConfirming}
                        >
                          {isConfirming ? 'Processing...' : 'Confirm and Trigger Workflow'}
                        </button>
                        <button
                          type="button"
                          className="chat-confirm-cancel"
                          onClick={() => confirmPlan(message.planId, false, message.id)}
                          disabled={isConfirming}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}

                  {message.kind === 'job_controls' && (
                    <div className="chat-job-controls">
                      {(message.jobs || []).map((job) => (
                        <div key={job.job_id} className="chat-job-row">
                          <span>{job.jira_ticket_id} ({job.status || 'queued'})</span>
                          <button
                            type="button"
                            onClick={() => cancelChatJob(message.id, job.job_id)}
                            disabled={['cancelled', 'cancelling', 'success', 'failed'].includes(String(job.status || '').toLowerCase())}
                          >
                            {String(job.status || '').toLowerCase() === 'cancelling' ? 'Cancelling...' : 'Cancel'}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </article>
              ))}

              {isSending && streamStatus && (
                <article className="chat-message chat-message--assistant chat-message--typing">
                  <header>Copilot</header>
                  <p>{streamStatus}</p>
                </article>
              )}
            </div>

            <form className="chat-composer chat-composer--floating" onSubmit={sendMessage}>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Ask to groom Jira issues or trigger workflow with confirmation..."
                rows={4}
              />

              <div className="chat-composer-corner">
                <select value={selectedAgent} onChange={(e) => setSelectedAgent(e.target.value)}>
                  {availableAgents.map((agent) => (
                    <option key={agent} value={agent}>{agent}</option>
                  ))}
                </select>
                <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
                  <option value="">Auto</option>
                  {availableModels.map((model) => (
                    <option key={model.id} value={model.id}>{model.name}</option>
                  ))}
                </select>
                <button
                  type="button"
                  className="chat-model-refresh icon-refresh-button"
                  onClick={onRefreshModels}
                  disabled={modelsLoading}
                  title="Refresh model list used by chat composer"
                  aria-label="Refresh chat model list"
                >
                  <span className={`icon-refresh-glyph${modelsLoading ? ' is-spinning' : ''}`} aria-hidden="true">⟳</span>
                </button>
                <button type="submit" disabled={!canSend}>
                  {isSending ? 'Sending...' : 'Send'}
                </button>
              </div>
            </form>

            {streamPhases.length > 0 && (
              <section className="chat-phases" aria-live="polite">
                <strong>{isSending ? 'Live phase timeline' : 'Latest run timeline'}</strong>
                <ol>
                  {streamPhases.map((phase, idx) => (
                    <li key={phase.id} className={idx === streamPhases.length - 1 && isSending ? 'chat-phase--active' : ''}>
                      {phase.label}
                    </li>
                  ))}
                </ol>
              </section>
            )}

            {chatError && <p className="chat-error">{chatError}</p>}
          </div>
        </div>
      </section>
    </div>
  )
}
