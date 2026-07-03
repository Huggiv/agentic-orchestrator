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

const deriveMessageKind = (message) => {
  const payload = message?.payload && typeof message.payload === 'object' ? message.payload : null
  if (message?.kind && message.kind !== 'text') return message.kind
  if (payload?.orchestration_payload || payload?.trigger_state === 'awaiting_confirmation') {
    return 'session_confirmation'
  }
  if (payload?.grooming) {
    return 'grooming_review'
  }
  return message?.kind || 'text'
}

const mapServerMessage = (message) => ({
  id: message.id,
  role: message.role === 'assistant' ? 'assistant' : 'user',
  kind: deriveMessageKind(message),
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

const formatClockTime = (timestampMs) => {
  if (!timestampMs) return ''
  return new Date(timestampMs).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })
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
  onModelDropdownFocus,
  onJobsQueued,
}) {
  const [sessions, setSessions] = useState([])
  const [activeSessionId, setActiveSessionId] = useState('')
  const [draft, setDraft] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [isConfirming, setIsConfirming] = useState(false)
  const [isAssigning, setIsAssigning] = useState(false)
  const [sessionsExpanded, setSessionsExpanded] = useState(false)
  const [chatError, setChatError] = useState('')
  const [streamStatus, setStreamStatus] = useState('')
  const listRef = useRef(null)

  const ensureRemoteSession = async () => {
    const current = sessions.find((item) => item.id === activeSessionId)
    if (!current) {
      throw new Error('No active chat session available')
    }
    if (current.remoteId) return current.remoteId

    const created = await createChatSession({
      title: current.title,
      mode: 'interactive',
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
  const openSessionCount = sessions.filter((session) => session.status !== 'closed').length

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
    setChatError('')
  }

  const prepareSessionTrigger = async (messageId) => {
    if (isAssigning) return
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
        triggerState: prepared?.trigger_state?.status || 'awaiting_confirmation',
        messages: [
          ...session.messages.map((message) =>
            message.id === messageId
              ? {
                  ...message,
                  resolved: true,
                  preparedPayload: prepared?.orchestration_payload || null,
                }
              : message
          ),
          prepared?.assistant_message
            ? mapServerMessage(prepared.assistant_message)
            : {
                id: `assistant-prepare-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                role: 'assistant',
                kind: 'session_confirmation',
                createdAt: Date.now(),
                resolved: false,
                content: 'Trigger payload prepared. Confirm launch.',
                payload: {
                  orchestration_payload: prepared?.orchestration_payload || null,
                  trigger_state: prepared?.trigger_state?.status || 'awaiting_confirmation',
                },
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
          ? messagesPayload.map(mapServerMessage)
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
    if (!listRef.current) return
    listRef.current.scrollTop = listRef.current.scrollHeight
  }, [orderedMessages, streamStatus])

  const replaceAssistantMessage = (assistantId, message) => {
    const normalized = message ? mapServerMessage(message) : null
    updateActiveSession((session) => ({
      ...session,
      messages: session.messages.map((message) =>
        message.id === assistantId
          ? {
              ...message,
              ...(normalized || {}),
              content: normalized?.content || message.content || '',
            }
          : message
      ),
    }))
  }

  const sendMessage = async (event) => {
    event.preventDefault()
    if (!canSend) return

    const userMessage = draft.trim()
    setDraft('')
    setChatError('')
    setStreamStatus('Processing message...')
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
      const data = await sendChatSessionMessage(remoteId, {
        message: userMessage,
        mode: 'interactive',
        model: selectedModel || null,
        client_context: {
          active_repository: repository,
          active_branch: 'development',
          reviewer: reviewer || null,
          selected_agent: selectedAgent || null,
        },
      })

      if ((data?.assistant_message?.content || '').trim()) {
        replaceAssistantMessage(assistantId, data.assistant_message)
      } else {
        replaceAssistantMessage(assistantId, {
          id: assistantId,
          role: 'assistant',
          kind: 'text',
          created_at: new Date().toISOString(),
          content: 'Done.',
          payload: null,
        })
      }
      updateActiveSession((session) => ({
        ...session,
        triggerState: data?.trigger_state?.status || session.triggerState || 'draft',
        grooming: data?.assistant_message?.payload?.grooming || session.grooming || null,
      }))
    } catch (err) {
      setChatError(err.message)
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

  const handleComposerKeyDown = (event) => {
    if (event.key !== 'Enter' || event.shiftKey) return
    event.preventDefault()
    if (!canSend) return
    sendMessage(event)
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

  return (
    <section className="panel chat-panel chat-panel--tab">
        <div className="chat-header">
          <div className="chat-header-title">
            <strong>Copilot Chat</strong>
            <span>Session-first orchestration assistant</span>
          </div>
          <div className="chat-header-actions">
            <span className="chat-header-chip">{openSessionCount} open</span>
            <button type="button" onClick={createNewSession}>New</button>
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
          <div className="chat-main">
            <div className="chat-thread" ref={listRef}>
              {orderedMessages.map((message) => (
                <article key={message.id} className={`chat-message chat-message--${message.role}`}>
                  <header>
                    <span>{message.role === 'assistant' ? 'Copilot' : 'You'}</span>
                    <time>{formatClockTime(message.createdAt)}</time>
                  </header>
                  <p>{message.content}</p>

                  {message.kind === 'grooming_review' && (
                    <div className="chat-grooming-review">
                      <p><strong>Recommended flow:</strong> {message.payload?.grooming?.recommended_template || '-'}</p>
                      <p>{message.payload?.grooming?.recommendation_rationale || ''}</p>
                      {(message.payload?.grooming?.missing_fields || []).length > 0 && (
                        <p><strong>Follow-up:</strong> {message.payload?.grooming?.follow_up_question || 'Please provide remaining fields.'}</p>
                      )}
                      {(message.payload?.grooming?.missing_fields || []).length === 0 && (
                        <div className="chat-confirm-actions">
                          <button
                            type="button"
                            onClick={() => prepareSessionTrigger(message.id)}
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
                      <p><strong>Review this implementation plan and confirm to trigger workflow.</strong></p>
                      <div className="chat-confirm-actions">
                        <button
                          type="button"
                          onClick={() => confirmSessionTrigger(message.id, activeSession?.remoteId, true)}
                          disabled={isConfirming}
                        >
                          {isConfirming ? 'Processing...' : 'Confirm and Trigger Workflow'}
                        </button>
                        <button
                          type="button"
                          className="chat-confirm-cancel"
                          onClick={() => confirmSessionTrigger(message.id, activeSession?.remoteId, false)}
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

            <form className="chat-composer chat-composer--embedded" onSubmit={sendMessage}>
              <div className="chat-composer-shell">
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  placeholder="Ask about Jira tickets, refine requirements, and confirm workflow triggers here..."
                  rows={4}
                />

                <div className="chat-composer-toolbar">
                  <div className="chat-composer-controls">
                    <label className="chat-composer-control" title="Selected agent">
                      <span className="chat-control-icon" aria-hidden="true">🤖</span>
                      <select value={selectedAgent} onChange={(e) => setSelectedAgent(e.target.value)}>
                        {availableAgents.map((agent) => (
                          <option key={agent} value={agent}>{agent}</option>
                        ))}
                      </select>
                    </label>
                    <label className="chat-composer-control" title="Selected model">
                      <span className="chat-control-icon" aria-hidden="true">🧠</span>
                      <select value={selectedModel} onFocus={() => onModelDropdownFocus?.()} onChange={(e) => setSelectedModel(e.target.value)}>
                        <option value="">Auto</option>
                        {availableModels.map((model) => (
                          <option key={model.id} value={model.id}>{model.name}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="chat-composer-actions">
                    <button type="submit" disabled={!canSend} title="Send message" aria-label="Send message">
                      <span aria-hidden="true">➤</span>
                    </button>
                  </div>
                </div>
              </div>
              <p className="chat-composer-hint">Enter to send. Shift+Enter for newline.</p>
            </form>

            {chatError && <p className="chat-error">{chatError}</p>}
          </div>

          <aside className={`chat-sessions chat-sessions--right${sessionsExpanded ? ' is-expanded' : ' is-collapsed'}`}>
            <button
              type="button"
              className="chat-sessions-toggle"
              onClick={() => setSessionsExpanded((prev) => !prev)}
              title={sessionsExpanded ? 'Collapse recent chats' : 'Expand recent chats'}
              aria-label={sessionsExpanded ? 'Collapse recent chats' : 'Expand recent chats'}
            >
              <span aria-hidden="true">{sessionsExpanded ? '⟩' : '⟨'}</span>
              {sessionsExpanded && <span>Recent Chats</span>}
            </button>

            {sessionsExpanded && (
              <>
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
              </>
            )}
          </aside>
        </div>
      </section>
  )
}
