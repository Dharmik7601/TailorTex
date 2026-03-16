export default function DownloadButton({ jobId, companyName }) {
  return (
    <a
      href={`/download/${jobId}`}
      download={`${companyName}_Resume.pdf`}
      className="download-btn"
    >
      Download PDF
    </a>
  )
}
