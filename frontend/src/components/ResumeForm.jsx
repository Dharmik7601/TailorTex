export default function ResumeForm({ onSubmit, disabled }) {
  function handleSubmit(e) {
    e.preventDefault()
    const form = e.target
    const formData = new FormData()
    formData.append('resume_file', form.resume_file.files[0])
    formData.append('job_description', form.job_description.value)
    formData.append('company_name', form.company_name.value)
    formData.append('use_constraints', form.use_constraints.checked)
    formData.append('use_projects', form.use_projects.checked)
    onSubmit(formData)
  }

  return (
    <form onSubmit={handleSubmit} className="form">
      <div className="field">
        <label htmlFor="resume_file">Master Resume (.tex)</label>
        <input id="resume_file" name="resume_file" type="file" accept=".tex" required />
      </div>

      <div className="field">
        <label htmlFor="job_description">Job Description</label>
        <textarea
          id="job_description"
          name="job_description"
          rows={10}
          placeholder="Paste the job description here..."
          required
        />
      </div>

      <div className="field">
        <label htmlFor="company_name">Company Name</label>
        <input
          id="company_name"
          name="company_name"
          type="text"
          placeholder="e.g. Google"
          required
        />
      </div>

      <div className="checkboxes">
        <label>
          <input type="checkbox" name="use_constraints" defaultChecked />
          Include constraints
        </label>
        <label>
          <input type="checkbox" name="use_projects" defaultChecked />
          Include extra projects
        </label>
      </div>

      <button type="submit" disabled={disabled}>
        {disabled ? 'Generating...' : 'Generate Resume'}
      </button>
    </form>
  )
}
