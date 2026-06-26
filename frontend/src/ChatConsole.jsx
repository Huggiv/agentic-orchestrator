import { useEffect, useMemo, useRef, useState } from 'react'

const initialAssistantMessage = {
  id: 'assistant-initial',
  role: 'assistant',
  content:
    'I can run one or more Jira-driven workflows from chat. Include ticket keys like AGENT_FLOW-101 and your grooming guidance in one prompt.',
}

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
  onJobsQueued,
}) {
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState([initialAssistantMessage])
  const [isSending, setIsSending] = useState(false)
  const [isConfirming, setIsConfirming] = useState(false)
  const [chatError, setChatError] = useState('')
  const [streamStatus, setStreamStatus] = useState('')
  const [streamPhases, setStreamPhases] = useState([])
  const [pendingPlan, setPendingPlan] = useState(null)
  const listRef = useRef(null)

  const canSend = useMemo(() => {
    return Boolean(draft.trim()) && Boolean(repository.trim()) && !isSending && !isConfirming
  }, [draft, repository, isSending, isConfirming])

  useEffect(() => {
    if (!listRef.current) return
    listRef.current.scrollTop = listRef.current.scrollHeight
  }, [messages])

  const appendAssistantDelta = (assistantId, delta) => {
    if (!delta) return
    setMessages((prev) =>
      prev.map((message) =>
        message.id === assistantId
          ? { ...message, content: `${message.content || ''}${delta}` }
          : message
      )
    )
  }

  const setAssistantText = (assistantId, text) => {
    setMessages((prev) =>
      prev.map((message) =>
        message.id === assistantId
          ? { ...message, content: text || '' }
          : message
      )
    )
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
    setMessages((prev) => [
      ...prev,
      { id: userId, role: 'user', content: userMessage },
      { id: assistantId, role: 'assistant', content: '' },
    ])

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
        setPendingPlan({
          id: data.plan_id,
          groomedIssue: data.groomed_issue || '',
          tickets: Array.isArray(data.tickets) ? data.tickets : [],
        })
      } else {
        setPendingPlan(null)
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
      setMessages((prev) => {
        const next = [...prev]
        const idx = next.findIndex((message) => message.id === assistantId)
        if (idx >= 0) {
          next[idx] = {
            ...next[idx],
            content: 'I could not process that prompt. Please verify ticket keys and try again.',
          }
          return next
        }
        next.push({
          id: `assistant-error-${Date.now()}`,
          role: 'assistant',
          content: 'I could not process that prompt. Please verify ticket keys and try again.',
        })
        return next
      })
    } finally {
      setStreamStatus('')
      setIsSending(false)
    }
  }

  const confirmPlan = async (confirm) => {
    if (!pendingPlan?.id) return

    setIsConfirming(true)
    setChatError('')
    const assistantId = `assistant-confirm-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setMessages((prev) => [...prev, { id: assistantId, role: 'assistant', content: '' }])
    try {
      const response = await fetch('/api/chat/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: pendingPlan.id, confirm }),
      })
      const raw = await response.text()
      const data = parseApiPayload(raw)
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to confirm grooming plan')
      }

      if (confirm) {
        const queuedJobs = Array.isArray(data.queued_jobs) ? data.queued_jobs : []
        if (queuedJobs.length > 0) onJobsQueued(queuedJobs)
      }

      setAssistantText(assistantId, data.assistant_message || (confirm ? 'Confirmed.' : 'Cancelled.'))
      setPendingPlan(null)
    } catch (err) {
      setChatError(err.message)
      setAssistantText(assistantId, 'I could not process your confirmation right now.')
    } finally {
      setIsConfirming(false)
    }
  }

  return (
    <section className="panel chat-panel">
      <div className="chat-settings">
        <label>
          Repository
          <input value={repository} onChange={(e) => setRepository(e.target.value)} placeholder="owner/repo" required />
        </label>

        <label>
          Agent
          <select value={selectedAgent} onChange={(e) => setSelectedAgent(e.target.value)}>
            {availableAgents.map((agent) => (
              <option key={agent} value={agent}>{agent}</option>
            ))}
          </select>
        </label>

        <label>
          Model
          <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
            <option value="">Auto</option>
            {availableModels.map((model) => (
              <option key={model.id} value={model.id}>{model.name}</option>
            ))}
          </select>
        </label>

        <label>
          Reviewer
          <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="teammate-name" />
        </label>
      </div>

      <div className="chat-thread" ref={listRef}>
        {isSending && streamStatus && (
          <article className="chat-message chat-message--assistant chat-message--typing">
            <header>Copilot</header>
            <p>{streamStatus}</p>
          </article>
        )}
        {messages.map((message, idx) => (
          <article
            key={`${message.role}-${idx}`}
            className={`chat-message chat-message--${message.role}`}
          >
            <header>{message.role === 'assistant' ? 'Copilot' : 'You'}</header>
            <p>{message.content}</p>
          </article>
        ))}
      </div>

      <form className="chat-composer" onSubmit={sendMessage}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Example: Run AGENT_FLOW-101 and AGENT_FLOW-102. Focus on robust error handling and add tests."
          rows={4}
        />
        <div className="chat-actions">
          <small>Use Shift+Enter for a new line. Include one or more Jira tickets in the prompt.</small>
          <button type="submit" disabled={!canSend}>{isSending ? 'Sending...' : 'Send'}</button>
        </div>
      </form>

      {pendingPlan && (
        <section className="chat-confirm-panel">
          <strong>Groomed issue ready for confirmation</strong>
          <p>
            Tickets: {pendingPlan.tickets.join(', ')}
          </p>
          <pre>{pendingPlan.groomedIssue || 'No groomed issue text available.'}</pre>
          <div className="chat-confirm-actions">
            <button type="button" onClick={() => confirmPlan(true)} disabled={isConfirming}>
              {isConfirming ? 'Processing...' : 'Confirm and Trigger Workflow'}
            </button>
            <button type="button" onClick={() => confirmPlan(false)} disabled={isConfirming} className="chat-confirm-cancel">
              Cancel
            </button>
          </div>
        </section>
      )}

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
    </section>
  )
}
