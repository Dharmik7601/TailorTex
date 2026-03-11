const API = 'http://localhost:8001';
const $ = id => document.getElementById(id);

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
          // Job description selectors (ordered by specificity)
          const jdSelectors = [
            '.jobs-description__content .jobs-box__html-content', // LinkedIn
            '.jobs-description-content__text',
            '#jobDescriptionText',                                 // Indeed
            '#content .job__description',                         // Greenhouse
            '.posting-page .posting-description',                 // Lever
            '[data-automation-id="job-description"]',             // Workday
            '[class*="jobDescriptionContent"]',                   // Glassdoor
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

          // Company name selectors
          const companySelectors = [
            '.jobs-unified-top-card__company-name a',             // LinkedIn
            '.jobs-unified-top-card__company-name',
            '[data-testid="inlineHeader-companyName"] a',         // Indeed
            '[data-testid="inlineHeader-companyName"]',
            '.company-name',                                       // Greenhouse
            '[data-automation-id="company"]',                     // Workday
            '[class*="employerName"]',                            // Glassdoor
          ];
          let company = '';
          for (const sel of companySelectors) {
            const el = document.querySelector(sel);
            if (el) {
              company = (el.getAttribute('alt') || el.innerText || '').trim();
              if (company) break;
            }
          }

          // Fallback: parse "Role at Company | Site" from page title
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

  // Switch to status view
  $('form-section').style.display = 'none';
  $('status-section').style.display = 'block';
  $('status-badge').textContent = 'Starting...';
  $('status-badge').className = 'running';
  $('logs').textContent = '';
  $('download-btn').style.display = 'none';

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
    if (!r.ok) throw new Error(`Server responded ${r.status}`);
    jobId = (await r.json()).job_id;
  } catch (e) {
    $('status-badge').textContent = 'Failed to start';
    $('status-badge').className = 'error';
    $('logs').textContent = String(e);
    return;
  }

  $('status-badge').textContent = 'Running...';
  streamLogs(jobId);
}

// ── Stream SSE logs ───────────────────────────────────────────────────────────
function streamLogs(jobId) {
  const logsEl = $('logs');
  const badge  = $('status-badge');
  const es     = new EventSource(`${API}/status/${jobId}`);

  es.onmessage = e => {
    logsEl.textContent += e.data + '\n';
    logsEl.scrollTop = logsEl.scrollHeight;
  };

  es.addEventListener('completed', () => {
    es.close();
    badge.textContent = 'Completed';
    badge.className = 'completed';
    const dl = $('download-btn');
    dl.href = `${API}/download/${jobId}`;
    dl.style.display = 'block';
  });

  es.addEventListener('error', ev => {
    if (ev.data) {
      es.close();
      badge.textContent = 'Error';
      badge.className = 'error';
    }
  });

  es.onerror = () => {
    es.close();
    if (badge.className !== 'completed') {
      badge.textContent = 'Connection lost';
      badge.className = 'error';
    }
  };
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  const online = await checkAPI();
  await loadResumes();

  // Auto-fill from page
  const { jd, company } = await extractFromPage();
  if (jd)      $('jd').value           = jd;
  if (company) $('company-name').value = company;

  $('generate-btn').disabled = !online;
  $('generate-btn').addEventListener('click', generate);

  $('back-btn').addEventListener('click', () => {
    $('status-section').style.display = 'none';
    $('form-section').style.display   = 'block';
  });
});
