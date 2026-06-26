import { useEffect, useMemo, useRef, useState } from 'react'

const initialAssistantMessage = {
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
  const [chatError, setChatError] = useState('')
  const listRef = useRef(null)

  const canSend = useMemo(() => {
    return Boolean(draft.trim()) && Boolean(repository.trim()) && !isSending
  }, [draft, repository, isSending])

  useEffect(() => {
    if (!listRef.current) return
    listRef.current.scrollTop = listRef.current.scrollHeight
  }, [messages])

  const sendMessage = async (event) => {
    event.preventDefault()
    if (!canSend) return

    const userMessage = draft.trim()
    setDraft('')
    setChatError('')
    setIsSending(true)
    setMessages((prev) => [...prev, { role: 'user', content: userMessage }])

    try {
      const response = await fetch('/api/chat/message', {
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

      const raw = await response.text()
      const data = parseApiPayload(raw)
      if (!response.ok) throw new Error(data.detail || 'Chat request failed')

      const queuedJobs = Array.isArray(data.queued_jobs) ? data.queued_jobs : []
      if (queuedJobs.length > 0) {
        onJobsQueued(queuedJobs)
      }
      setMessages((prev) => [...prev, { role: 'assistant', content: data.assistant_message || 'Done.' }])
    } catch (err) {
      setChatError(err.message)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: 'I could not process that prompt. Please verify ticket keys and try again.',
        },
      ])
    } finally {
      setIsSending(false)
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

      {chatError && <p className="chat-error">{chatError}</p>}
    </section>
  )
}
