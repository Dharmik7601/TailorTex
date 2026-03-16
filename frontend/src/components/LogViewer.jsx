import { useEffect, useRef } from 'react'

export default function LogViewer({ logs, status }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  if (logs.length === 0) return null

  return (
    <div className="log-viewer">
      <div className={`log-status status-${status}`}>
        {status === 'running' && '⏳ Running...'}
        {status === 'completed' && '✅ Completed'}
        {status === 'error' && '❌ Error'}
      </div>
      <pre className="log-output">
        {logs.map((line, i) => (
          <div key={i}>{line}</div>
        ))}
        <div ref={bottomRef} />
      </pre>
    </div>
  )
}
