const API = 'http://localhost:8001';
const STORAGE_KEY = 'ttjobs';
const MAX_SLOTS = 5;

const $ = id => document.getElementById(id);

// Module-level map of job_id -> EventSource to avoid duplicate SSE connections
const sseMap = new Map();

// Module-level log cache: job_id -> string[] (in-memory, per tab, per panel session)
// Log lines are never written to storage during streaming — only at job completion/error.
const logCache = new Map();

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

// ── HTML escaping ─────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Slot counter + generate button state ──────────────────────────────────────
function updateSlotCounter(jobs) {
  const activeCount = jobs.filter(j => j.status === 'queued' || j.status === 'running').length;
  $('slot-counter').textContent = `${activeCount} / ${MAX_SLOTS} slots used`;
  const btn = $('generate-btn');
  if (activeCount >= MAX_SLOTS) {
    btn.disabled = true;
    btn.title = 'Queue full';
  } else {
    btn.disabled = $('api-status').className === 'offline';
    btn.title = '';
  }
}

// ── Create a new job card DOM element (called once per job) ───────────────────
function createJobCard(job) {
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

  // Open PDF button (if already completed at creation time)
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

  // Populate log from storage if available (e.g. panel reopen after completion)
  if (job.log && job.log.length) {
    logsEl.textContent = job.log.join('\n');
  }

  return card;
}

// ── Patch an existing card with updated job state (no innerHTML reset) ────────
function patchJobCard(card, job) {
  // Update status badge text and class
  const statusBadge = card.querySelector('.status-badge');
  if (statusBadge) {
    statusBadge.textContent = job.status;
    const statusClass = {
      queued: 'status-queued',
      running: 'status-running',
      completed: 'status-completed',
      error: 'status-error',
    }[job.status] || '';
    statusBadge.className = `status-badge ${statusClass}`;
  }

  // Add Open PDF button if job just completed and button not yet present
  if (job.status === 'completed' && !card.querySelector('.job-open-btn')) {
    const btn = document.createElement('button');
    btn.className = 'job-open-btn';
    btn.textContent = 'Open PDF';
    btn.addEventListener('click', async () => {
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
    const body = card.querySelector('.job-card-body');
    if (body) body.appendChild(btn);
  }

  // Update log text only if no live SSE in this tab (e.g. cross-tab sync update)
  // When SSE is active, the onmessage handler writes directly to the log element.
  if (!sseMap.has(job.job_id) && job.log && job.log.length) {
    const logsEl = card.querySelector('.job-card-logs');
    if (logsEl) logsEl.textContent = job.log.join('\n');
  }
}

// ── Render queue (reconciler — reuses existing cards, no full DOM rebuild) ────
function renderQueue(jobs) {
  updateSlotCounter(jobs);

  const list = $('job-list');
  // Newest first
  const sorted = [...jobs].reverse();

  // For each job in display order: patch existing card or create a new one
  const orderedCards = sorted.map(job => {
    const existing = list.querySelector(`.job-card[data-job-id="${job.job_id}"]`);
    if (existing) {
      patchJobCard(existing, job);
      return existing;
    }
    return createJobCard(job);
  });

  // Remove cards for jobs that are no longer in the list (discarded)
  const currentIds = new Set(jobs.map(j => j.job_id));
  list.querySelectorAll('.job-card').forEach(card => {
    if (!currentIds.has(card.dataset.jobId)) card.remove();
  });

  // Re-append all cards in sorted order.
  // appendChild on an existing node moves it — preserving event listeners.
  // Appending in newest-first order yields newest at top of DOM.
  orderedCards.forEach(card => list.appendChild(card));

  // Start SSE for any active job without a connection in this tab
  for (const job of jobs) {
    if ((job.status === 'queued' || job.status === 'running') && !sseMap.has(job.job_id)) {
      attachSSE(job.job_id);
    }
  }
}

// ── SSE streaming per job ─────────────────────────────────────────────────────
function attachSSE(job_id) {
  if (sseMap.has(job_id)) return;

  // Initialize in-memory log buffer for this session
  logCache.set(job_id, []);

  const es = new EventSource(`${API}/status/${job_id}`);
  sseMap.set(job_id, es);

  es.onmessage = e => {
    logCache.get(job_id).push(e.data);
    // Write directly to the card's log element — no storage write, no renderQueue
    const card = document.querySelector(`.job-card[data-job-id="${job_id}"]`);
    if (card) {
      const logsEl = card.querySelector('.job-card-logs');
      logsEl.textContent += (logsEl.textContent ? '\n' : '') + e.data;
      logsEl.scrollTop = logsEl.scrollHeight;
    }
  };

  es.addEventListener('completed', async () => {
    es.close();
    sseMap.delete(job_id);
    const finalLog = logCache.get(job_id) || [];
    logCache.delete(job_id);
    await updateJobStatus(job_id, { status: 'completed', pdf_ready: true, log: finalLog });
  });

  es.addEventListener('error', async ev => {
    if (ev.data) {
      es.close();
      sseMap.delete(job_id);
      const finalLog = logCache.get(job_id) || [];
      logCache.delete(job_id);
      await updateJobStatus(job_id, { status: 'error', log: finalLog });
    }
  });

  es.onerror = async () => {
    const jobs = await getJobs();
    const job = jobs.find(j => j.job_id === job_id);
    if (job && job.status !== 'completed') {
      es.close();
      sseMap.delete(job_id);
      const finalLog = logCache.get(job_id) || [];
      logCache.delete(job_id);
      await updateJobStatus(job_id, { status: 'error', log: finalLog });
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

// ── Test exports (no-op in browser, used by Jest) ─────────────────────────────
if (typeof module !== 'undefined') {
  module.exports = {
    renderQueue, attachSSE, createJobCard, patchJobCard, updateSlotCounter,
    getJobs, saveJobs, escHtml, logCache, sseMap,
  };
}
