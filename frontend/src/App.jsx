import { useState, useRef } from 'react'
import ResumeForm from './components/ResumeForm'
import LogViewer from './components/LogViewer'
import DownloadButton from './components/DownloadButton'
import './App.css'

export default function App() {
  const [status, setStatus] = useState('idle') // idle | running | completed | error
  const [logs, setLogs] = useState([])
  const [jobId, setJobId] = useState(null)
  const [companyName, setCompanyName] = useState('')
  const eventSourceRef = useRef(null)

  async function handleSubmit(formData) {
    setLogs([])
    setJobId(null)
    setStatus('running')
    setCompanyName(formData.get('company_name'))

    try {
      const res = await fetch('/generate', { method: 'POST', body: formData })
      if (!res.ok) {
        const err = await res.text()
        setLogs([`Server error: ${err}`])
        setStatus('error')
        return
      }
      const { job_id } = await res.json()
      setJobId(job_id)
      listenToStatus(job_id)
    } catch (err) {
      setLogs([`Network error: ${err.message}`])
      setStatus('error')
    }
  }

  function listenToStatus(job_id) {
    if (eventSourceRef.current) eventSourceRef.current.close()

    const es = new EventSource(`/status/${job_id}`)
    eventSourceRef.current = es

    es.onmessage = (e) => {
      setLogs((prev) => [...prev, e.data])
    }

    es.addEventListener('completed', () => {
      setStatus('completed')
      es.close()
    })

    es.addEventListener('error', () => {
      if (es.readyState === EventSource.CLOSED) {
        setStatus('error')
      }
    })
  }

  return (
    <div className="container">
      <header>
        <h1>TailorTex</h1>
        <p>AI-powered LaTeX resume tailoring</p>
      </header>

      <ResumeForm onSubmit={handleSubmit} disabled={status === 'running'} />

      <LogViewer logs={logs} status={status} />

      {status === 'completed' && jobId && (
        <DownloadButton jobId={jobId} companyName={companyName} />
      )}
    </div>
  )
}
