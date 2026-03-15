const API = 'http://localhost:8001';
const STORAGE_KEY = 'ttjobs';
const MAX_SLOTS = 5;

const $ = id => document.getElementById(id);

// Module-level map of job_id -> EventSource to avoid duplicate SSE connections
const sseMap = new Map();

// ── API health check ──────────────────────────────────────────────────────────
async function checkAPI() {
  const badge = $('api-status');
  try {
    const r = await fetch(`${API}/health`, { signal: AbortSignal.timeout(3000) });
    if (r.ok) {
      badge.textContent = 'Online';
      badge.className = 'online';
      return true;
    }
  } catch (_) {}
  badge.textContent = 'Offline';
  badge.className = 'offline';
  return false;
}

// ── Populate base-resume dropdown from server ─────────────────────────────────
async function loadResumes() {
  const sel = $('resume-select');
  try {
    const r = await fetch(`${API}/resumes`);
    const data = await r.json();
    sel.innerHTML = '';
    data.resumes.forEach(name => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    });
  } catch (_) {
    sel.innerHTML = '<option value="">Could not load resumes</option>';
  }
}

// ── Scrape JD + company name from current tab ─────────────────────────────────
async function extractFromPage() {
  return new Promise(resolve => {
    chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
      if (!tab) return resolve({ jd: '', company: '' });
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const jdSelectors = [
            '.jobs-description__content .jobs-box__html-content',
            '.jobs-description-content__text',
            '#jobDescriptionText',
            '#content .job__description',
            '.posting-page .posting-description',
            '[data-automation-id="job-description"]',
            '[class*="jobDescriptionContent"]',
            '[class*="job-description"]',
            '[id*="job-description"]',
          ];
          let jd = '';
          for (const sel of jdSelectors) {
            const el = document.querySelector(sel);
            if (el && el.innerText.trim().length > 100) {
              jd = el.innerText.trim();
              break;
            }
          }

          const companySelectors = [
            '.jobs-unified-top-card__company-name a',
            '.jobs-unified-top-card__company-name',
            '[data-testid="inlineHeader-companyName"] a',
            '[data-testid="inlineHeader-companyName"]',
            '.company-name',
            '[data-automation-id="company"]',
            '[class*="employerName"]',
          ];
          let company = '';
          for (const sel of companySelectors) {
            const el = document.querySelector(sel);
            if (el) {
              company = (el.getAttribute('alt') || el.innerText || '').trim();
              if (company) break;
            }
          }

          if (!company) {
            const m = document.title.match(/ at ([^|–\-]+)/i);
            if (m) company = m[1].trim();
          }

          return { jd, company };
        }
      }, results => {
        if (chrome.runtime.lastError || !results?.[0]?.result) {
          resolve({ jd: '', company: '' });
        } else {
          resolve(results[0].result);
        }
      });
    });
  });
}

// ── Storage helpers ───────────────────────────────────────────────────────────
function getJobs() {
  return new Promise(resolve => {
    chrome.storage.local.get([STORAGE_KEY], result => {
      resolve(result[STORAGE_KEY] || []);
    });
  });
}

function saveJobs(jobs) {
  return new Promise(resolve => {
    chrome.storage.local.set({ [STORAGE_KEY]: jobs }, resolve);
  });
}

async function addJob(job) {
  const jobs = await getJobs();
  jobs.push(job);
  await saveJobs(jobs);
}

async function updateJobStatus(job_id, updates) {
  const jobs = await getJobs();
  const idx = jobs.findIndex(j => j.job_id === job_id);
  if (idx !== -1) {
    Object.assign(jobs[idx], updates);
    await saveJobs(jobs);
  }
}

async function removeJob(job_id) {
  const jobs = await getJobs();
  await saveJobs(jobs.filter(j => j.job_id !== job_id));
}

// ── Render queue ──────────────────────────────────────────────────────────────
function renderQueue(jobs) {
  const activeCount = jobs.filter(j => j.status === 'queued' || j.status === 'running').length;

  $('slot-counter').textContent = `${activeCount} / ${MAX_SLOTS} slots used`;

  // Disable generate button if full or API offline
  const btn = $('generate-btn');
  if (activeCount >= MAX_SLOTS) {
    btn.disabled = true;
    btn.title = 'Queue full';
  } else {
    // Re-enable only if API is online (check class on badge)
    btn.disabled = $('api-status').className === 'offline';
    btn.title = '';
  }

  const list = $('job-list');
  list.innerHTML = '';

  // Show newest first
  const sorted = [...jobs].reverse();

  for (const job of sorted) {
    const card = document.createElement('div');
    card.className = 'job-card';
    card.dataset.jobId = job.job_id;

    const statusClass = {
      queued: 'status-queued',
      running: 'status-running',
      completed: 'status-completed',
      error: 'status-error',
    }[job.status] || '';

    const methodLabel = job.method === 'claudecli' ? 'Claude' : 'Gemini';
    const methodClass = job.method === 'claudecli' ? 'badge-claude' : 'badge-gemini';

    card.innerHTML = `
      <div class="job-card-header">
        <div class="job-card-info">
          <span class="job-company">${escHtml(job.company)}</span>
          <span class="job-resume">${escHtml(job.resume_name || '')}</span>
        </div>
        <div class="job-card-meta">
          <span class="method-badge ${methodClass}">${methodLabel}</span>
          <span class="status-badge ${statusClass}">${job.status}</span>
          <button class="job-discard-btn" title="Discard">×</button>
        </div>
      </div>
      <div class="job-card-body">
        <button class="job-log-toggle">Logs ▸</button>
        <pre class="job-card-logs" style="display:none"></pre>
        ${job.status === 'completed' ? `<button class="job-open-btn">Open PDF</button>` : ''}
      </div>
    `;

    // Discard button
    card.querySelector('.job-discard-btn').addEventListener('click', () => {
      if (sseMap.has(job.job_id)) {
        sseMap.get(job.job_id).close();
        sseMap.delete(job.job_id);
      }
      removeJob(job.job_id);
    });

    // Log toggle
    const logsEl = card.querySelector('.job-card-logs');
    card.querySelector('.job-log-toggle').addEventListener('click', e => {
      if (!logsEl) return;
      const open = logsEl.style.display !== 'none';
      logsEl.style.display = open ? 'none' : 'block';
      e.target.textContent = open ? 'Logs ▸' : 'Logs ▾';
    });

    // Open PDF with system default viewer via server
    if (job.status === 'completed') {
      card.querySelector('.job-open-btn').addEventListener('click', async () => {
        try {
          const url = `${API}/open/${job.job_id}?company=${encodeURIComponent(job.company)}`;
          const r = await fetch(url);
          if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            alert(d.detail || 'Could not open PDF');
          }
        } catch (e) {
          alert('Could not open PDF: ' + e.message);
        }
      });
    }

    // Populate cached logs if available
    if (job.log && job.log.length) {
      logsEl.textContent = job.log.join('\n');
    }

    list.appendChild(card);

    // Start SSE if job is active
    if ((job.status === 'queued' || job.status === 'running') && !sseMap.has(job.job_id)) {
      attachSSE(job.job_id);
    }
  }
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── SSE streaming per job ─────────────────────────────────────────────────────
function attachSSE(job_id) {
  if (sseMap.has(job_id)) return;

  const es = new EventSource(`${API}/status/${job_id}`);
  sseMap.set(job_id, es);

  const logBuffer = [];

  es.onmessage = async e => {
    logBuffer.push(e.data);
    // Update log in card if visible
    const card = document.querySelector(`.job-card[data-job-id="${job_id}"]`);
    if (card) {
      const logsEl = card.querySelector('.job-card-logs');
      logsEl.textContent = logBuffer.join('\n');
      logsEl.scrollTop = logsEl.scrollHeight;
    }
    // Persist log lines to storage
    await updateJobStatus(job_id, { log: logBuffer });
  };

  es.addEventListener('completed', async () => {
    es.close();
    sseMap.delete(job_id);
    await updateJobStatus(job_id, { status: 'completed', pdf_ready: true });
  });

  es.addEventListener('error', async ev => {
    if (ev.data) {
      es.close();
      sseMap.delete(job_id);
      await updateJobStatus(job_id, { status: 'error' });
    }
  });

  es.onerror = async () => {
    const jobs = await getJobs();
    const job = jobs.find(j => j.job_id === job_id);
    if (job && job.status !== 'completed') {
      es.close();
      sseMap.delete(job_id);
      await updateJobStatus(job_id, { status: 'error' });
    }
  };
}

// ── Submit generation job ─────────────────────────────────────────────────────
async function generate() {
  const company    = $('company-name').value.trim();
  const jd         = $('jd').value.trim();
  const resumeName = $('resume-select').value;
  const method     = $('method-select').value;
  const useConstraints = $('use-constraints').checked;
  const useProjects    = $('use-projects').checked;

  if (!company)    return alert('Please enter a company / output title.');
  if (!jd)         return alert('Please enter or paste a job description.');
  if (!resumeName) return alert('No base resume selected.');

  const fd = new FormData();
  fd.append('company_name',    company);
  fd.append('job_description', jd);
  fd.append('resume_name',     resumeName);
  fd.append('method',          method);
  fd.append('use_constraints', useConstraints);
  fd.append('use_projects',    useProjects);

  let jobId;
  try {
    const r = await fetch(`${API}/generate`, { method: 'POST', body: fd });
    if (r.status === 429) {
      const data = await r.json();
      return alert(data.detail || 'Queue full. Please wait for a slot.');
    }
    if (!r.ok) throw new Error(`Server responded ${r.status}`);
    jobId = (await r.json()).job_id;
  } catch (e) {
    return alert(`Failed to start: ${e.message}`);
  }

  await addJob({
    job_id: jobId,
    company,
    resume_name: resumeName,
    method,
    status: 'queued',
    submitted_at: Date.now(),
    log: [],
  });

  // Clear form fields
  $('company-name').value = '';
  $('jd').value = '';
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  const online = await checkAPI();
  await loadResumes();

  const { jd, company } = await extractFromPage();
  if (jd)      $('jd').value           = jd;
  if (company) $('company-name').value = company;

  $('generate-btn').disabled = !online;
  $('generate-btn').addEventListener('click', generate);

  // Initial render from storage
  const jobs = await getJobs();
  renderQueue(jobs);

  // Cross-tab sync: re-render whenever storage changes
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && changes[STORAGE_KEY]) {
      renderQueue(changes[STORAGE_KEY].newValue || []);
    }
  });
});
