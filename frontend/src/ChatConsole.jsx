import { useEffect, useMemo, useRef, useState } from 'react'
import {
  archiveChatSession,
  confirmChatSessionTrigger,
  createChatSession,
  getChatSessionMessages,
  listChatSessions,
  prepareChatSessionTrigger,
  sendChatSessionMessage,
} from './services/chat'

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
  remoteId: null,
  remoteLoaded: true,
  title: 'New Chat',
  status: 'open',
  triggerState: 'draft',
  updatedAt: Date.now(),
  grooming: null,
  messages: [createInitialAssistantMessage()],
})

const toLocalTimestamp = (value) => {
  if (!value) return Date.now()
  const parsed = Date.parse(value)
  if (Number.isNaN(parsed)) return Date.now()
  return parsed
}

const mapRemoteMessage = (message) => ({
  id: message.id,
  role: message.role === 'assistant' ? 'assistant' : 'user',
  kind: message.kind || 'text',
  createdAt: toLocalTimestamp(message.created_at),
  content: message.content || '',
  payload: message.payload || null,
})

const formatTimeAgo = (timestampMs) => {
  if (!timestampMs) return 'just now'
  const deltaMs = Date.now() - timestampMs
  const deltaMin = Math.max(1, Math.floor(deltaMs / 60000))
  if (deltaMin < 60) return `${deltaMin}m ago`
  const deltaHours = Math.floor(deltaMin / 60)
  if (deltaHours < 24) return `${deltaHours}h ago`
  const deltaDays = Math.floor(deltaHours / 24)
  return `${deltaDays}d ago`
}

const triggerStateLabel = (state) => {
  const value = String(state || 'draft').replace(/_/g, ' ')
  return value.charAt(0).toUpperCase() + value.slice(1)
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
  const [chatMode, setChatMode] = useState('support')
  const [isOpen, setIsOpen] = useState(false)
  const [launcherPosition, setLauncherPosition] = useState(null)
  const dragRef = useRef({ dragging: false, offsetX: 0, offsetY: 0 })
  const [sessions, setSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState('')
  const [draft, setDraft] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [isConfirming, setIsConfirming] = useState(false)
  const [isAssigning, setIsAssigning] = useState(false)
  const [chatError, setChatError] = useState('')
  const [streamStatus, setStreamStatus] = useState('')
  const [streamPhases, setStreamPhases] = useState([])
  const listRef = useRef(null)

  const ensureRemoteSession = async () => {
    const current = sessions.find((item) => item.id === activeSessionId)
    if (!current) {
      throw new Error('No active chat session available')
    }
    if (current.remoteId) return current.remoteId

    const created = await createChatSession({
      title: current.title,
      mode: chatMode,
      model: selectedModel || null,
      client_context: {
        active_repository: repository,
        active_branch: 'development',
        reviewer: reviewer || null,
        selected_agent: selectedAgent || null,
      },
    })

    const remoteId = created.session_id
    updateActiveSession((session) => ({ ...session, remoteId }))
    return remoteId
  }

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) || null,
    [sessions, activeSessionId]
  )

  const messages = activeSession?.messages || []
  const activeGrooming = activeSession?.grooming || null

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

    return
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

  const assignGrooming = async (messageId, groomingPayload) => {
    if (!groomingPayload || !groomingPayload.schema || isAssigning) return
    if (!activeSessionId) return
    setIsAssigning(true)
    setChatError('')

    try {
      const remoteId = await ensureRemoteSession()
      const prepared = await prepareChatSessionTrigger(remoteId, {
        repository,
        base_branch: 'development',
        reviewer: reviewer || null,
        selected_agent: selectedAgent || null,
        selected_model: selectedModel || null,
      })

      updateActiveSession((session) => ({
        ...session,
        messages: session.messages.map((message) =>
          message.id === messageId
            ? {
                ...message,
                resolved: true,
                preparedPayload: prepared?.orchestration_payload || null,
              }
            : message
        ),
      }))

      updateActiveSession((session) => ({
        ...session,
        messages: [
          ...session.messages,
          {
            id: `assistant-prepare-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            role: 'assistant',
            kind: 'session_confirmation',
            createdAt: Date.now(),
            resolved: false,
            remoteSessionId: remoteId,
            content: prepared?.assistant_message?.content || 'Trigger payload prepared. Confirm launch.',
            payload: prepared?.orchestration_payload || null,
          },
        ],
      }))
    } catch (err) {
      setChatError(err.message)
    } finally {
      setIsAssigning(false)
    }
  }

  const deleteSession = (sessionId) => {
    const current = sessions.find((session) => session.id === sessionId)
    Promise.resolve()
      .then(async () => {
        if (current?.remoteId) {
          await archiveChatSession(current.remoteId)
        }
      })
      .finally(() => {
        const next = sessions.filter((session) => session.id !== sessionId)
        if (next.length === 0) {
          const replacement = createSession()
          persistSessions([replacement], replacement.id)
          return
        }
        const nextActive = sessionId === activeSessionId ? next[0].id : activeSessionId
        persistSessions(next, nextActive)
      })
  }

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const remote = await listChatSessions(MAX_CHAT_SESSIONS)
        if (!alive) return

        if (Array.isArray(remote) && remote.length > 0) {
          const mapped = remote.slice(0, MAX_CHAT_SESSIONS).map((session) => ({
            id: `session-${session.id}`,
            remoteId: session.id,
            remoteLoaded: false,
            title: session.title || 'Chat',
            status: session.status || 'open',
            triggerState: session?.metadata?.trigger_state || 'draft',
            updatedAt: toLocalTimestamp(session.updated_at),
            grooming: session?.metadata?.grooming_state
              ? {
                  schema: {
                    problem: session.metadata.grooming_state.problem || '',
                    user_impact: session.metadata.grooming_state.user_impact || '',
                    goals: session.metadata.grooming_state.goals || [],
                    constraints: session.metadata.grooming_state.constraints || [],
                    acceptance_criteria: session.metadata.grooming_state.acceptance_criteria || [],
                  },
                  pending_field: session.metadata.grooming_state.pending_field || null,
                  missing_fields: [],
                }
              : null,
            messages: [createInitialAssistantMessage()],
          }))
          setSessions(mapped)
          setActiveSessionId(mapped[0].id)
          return
        }
      } catch {
        if (!alive) return
        const fresh = createSession()
        setSessions([fresh])
        setActiveSessionId(fresh.id)
      }
    }

    load()
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    const current = sessions.find((session) => session.id === activeSessionId)
    if (!current?.remoteId || current.remoteLoaded) return

    let alive = true
    getChatSessionMessages(current.remoteId)
      .then((messagesPayload) => {
        if (!alive) return
        const mappedMessages = Array.isArray(messagesPayload) && messagesPayload.length > 0
          ? messagesPayload.map(mapRemoteMessage)
          : [createInitialAssistantMessage()]

        setSessions((prev) =>
          prev.map((session) =>
            session.id === activeSessionId
              ? {
                  ...session,
                  remoteLoaded: true,
                  messages: mappedMessages,
                  status: 'open',
                  updatedAt: Date.now(),
                }
              : session
          )
        )
      })
      .catch(() => {
        if (!alive) return
        setSessions((prev) =>
          prev.map((session) =>
            session.id === activeSessionId
              ? { ...session, remoteLoaded: true }
              : session
          )
        )
      })

    return () => {
      alive = false
    }
  }, [sessions, activeSessionId])

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

  const sendMessage = async (event) => {
    event.preventDefault()
    if (!canSend) return

    const userMessage = draft.trim()
    setDraft('')
    setChatError('')
    setStreamStatus('Processing message...')
    setStreamPhases([{ id: `phase-${Date.now()}`, label: 'Processing message...' }])
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
      const remoteId = await ensureRemoteSession()
      addPhase('Sending to session API')
      const data = await sendChatSessionMessage(remoteId, {
        message: userMessage,
        mode: chatMode,
        model: selectedModel || null,
        client_context: {
          active_repository: repository,
          active_branch: 'development',
          reviewer: reviewer || null,
          selected_agent: selectedAgent || null,
        },
      })

      const groomingSchema = data?.grooming && typeof data.grooming === 'object' ? data.grooming : null
      const missingFields = Array.isArray(data?.assistant_message?.payload?.grooming?.missing_fields)
        ? data.assistant_message.payload.grooming.missing_fields
        : []
      const isComplete = missingFields.length === 0 && Boolean(groomingSchema)

      if (chatMode === 'grooming' && groomingSchema) {
        updateActiveSession((session) => ({
          ...session,
          triggerState: data?.trigger_state?.status || session.triggerState || 'grooming',
          grooming: {
            schema: groomingSchema,
            missing_fields: missingFields,
            is_complete: isComplete,
            recommended_template:
              data?.assistant_message?.payload?.grooming?.recommended_template ||
              (data?.assistant_message?.payload?.trigger_state === 'ready_to_trigger' ? 'feature' : null),
            recommendation_rationale: data?.assistant_message?.payload?.grooming?.recommendation_rationale || '',
            pending_field: data?.assistant_message?.payload?.grooming?.pending_field || null,
            follow_up_question: data?.assistant_message?.payload?.grooming?.follow_up_question || null,
          },
          messages: [
            ...session.messages,
            {
              id: `grooming-review-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              role: 'assistant',
              kind: 'grooming_review',
              createdAt: Date.now(),
              resolved: false,
              content: JSON.stringify(groomingSchema, null, 2),
              grooming: {
                schema: groomingSchema,
                missing_fields: missingFields,
                is_complete: isComplete,
                recommended_template: data?.assistant_message?.payload?.grooming?.recommended_template || 'feature',
                recommendation_rationale: data?.assistant_message?.payload?.grooming?.recommendation_rationale || '',
                follow_up_question: data?.assistant_message?.payload?.grooming?.follow_up_question || null,
              },
            },
          ],
        }))
      }

      if ((data?.assistant_message?.content || '').trim()) {
        setAssistantText(assistantId, data.assistant_message.content)
      } else {
        setAssistantText(assistantId, 'Done.')
      }
      updateActiveSession((session) => ({
        ...session,
        triggerState: data?.trigger_state?.status || session.triggerState || 'draft',
      }))
      addPhase('Completed response generation')
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

  const confirmSessionTrigger = async (messageId, remoteSessionId, confirm) => {
    if (!remoteSessionId || isConfirming) return

    setIsConfirming(true)
    setChatError('')
    try {
      if (!confirm) {
        updateActiveSession((session) => ({
          ...session,
          triggerState: 'ready_to_trigger',
          messages: session.messages.map((msg) =>
            msg.id === messageId ? { ...msg, resolved: true, content: 'Trigger cancelled.' } : msg
          ),
        }))
        return
      }

      const data = await confirmChatSessionTrigger(remoteSessionId, {
        confirm: true,
        idempotency_key: `chat-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      })
      const job = data?.job
      if (job?.job_id) {
        onJobsQueued([{ job_id: job.job_id, status: job.status || 'queued', jira_ticket_id: 'SESSION' }])
      }
      updateActiveSession((session) => ({
        ...session,
        triggerState: 'triggered',
        messages: session.messages.map((msg) =>
          msg.id === messageId
            ? {
                ...msg,
                resolved: true,
                content: `Confirmed. Workflow queued with job ${job?.job_id || '-'}.`,
              }
            : msg
        ),
      }))
    } catch (err) {
      setChatError(err.message)
    } finally {
      setIsConfirming(false)
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
            Mode
            <select value={chatMode} onChange={(e) => setChatMode(e.target.value)}>
              <option value="support">Support</option>
              <option value="grooming">Grooming</option>
            </select>
          </label>

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
                  <span className="chat-session-title">{session.title || 'Chat'}</span>
                  <span className="chat-session-meta-row">
                    <span className={`chat-session-badge chat-session-badge--${session.status === 'closed' ? 'closed' : 'open'}`}>
                      {session.status === 'closed' ? 'Closed' : 'Open'}
                    </span>
                    <span className="chat-session-trigger">{triggerStateLabel(session.triggerState)}</span>
                  </span>
                  <span className="chat-session-updated">Updated {formatTimeAgo(session.updatedAt)}</span>
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

                  {message.kind === 'grooming_review' && (
                    <div className="chat-grooming-review">
                      <pre>{message.content || 'No grooming summary available.'}</pre>
                      <p><strong>Recommended flow:</strong> {message.grooming?.recommended_template || '-'}</p>
                      <p>{message.grooming?.recommendation_rationale || ''}</p>
                      {(message.grooming?.missing_fields || []).length > 0 && (
                        <p><strong>Follow-up:</strong> {message.grooming?.follow_up_question || 'Please provide remaining fields.'}</p>
                      )}
                      {(message.grooming?.missing_fields || []).length === 0 && (
                        <div className="chat-confirm-actions">
                          <button
                            type="button"
                            onClick={() => assignGrooming(message.id, message.grooming)}
                            disabled={isAssigning || message.resolved}
                          >
                            {isAssigning ? 'Preparing...' : (message.resolved ? 'Prepared' : 'Prepare Trigger')}
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                  {message.kind === 'session_confirmation' && !message.resolved && (
                    <div className="chat-inline-confirm">
                      <p><strong>Prepared payload:</strong></p>
                      <pre>{JSON.stringify(message.payload || {}, null, 2)}</pre>
                      <div className="chat-confirm-actions">
                        <button
                          type="button"
                          onClick={() => confirmSessionTrigger(message.id, message.remoteSessionId, true)}
                          disabled={isConfirming}
                        >
                          {isConfirming ? 'Processing...' : 'Confirm and Trigger Workflow'}
                        </button>
                        <button
                          type="button"
                          className="chat-confirm-cancel"
                          onClick={() => confirmSessionTrigger(message.id, message.remoteSessionId, false)}
                          disabled={isConfirming}
                        >
                          Cancel
                        </button>
                      </div>
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
                placeholder={
                  chatMode === 'grooming'
                    ? 'Describe requirements, or answer follow-up prompts to complete grooming...'
                    : 'Ask to groom Jira issues or trigger workflow with confirmation...'
                }
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
