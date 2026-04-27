# Feature: Chrome Extension

## Files Involved

| File | Role |
|------|------|
| `frontend/extension/manifest.json` | MV3 manifest — declares permissions, side panel, background |
| `frontend/extension/background.js` | Opens side panel on toolbar icon click |
| `frontend/extension/popup.html` | Side panel HTML — form, queue section, output browser, details view |
| `frontend/extension/popup.js` | All extension logic — state, SSE, DOM reconciler, API calls |
| `frontend/extension/popup.css` | Dark theme styles |

No automated tests. Verified manually via Chrome DevTools and extension reload.

---

## Purpose

The extension is a Chrome MV3 side panel that lets users submit resume generation jobs from any job listing page, monitor live progress via SSE, view structured resume details, and manage previously generated resumes — all without leaving the job listing.

---

## MV3 Side Panel Setup

```json
// manifest.json (key fields)
{
  "manifest_version": 3,
  "permissions": ["sidePanel", "storage", "scripting", "tabs", "activeTab"],
  "side_panel": { "default_path": "popup.html" },
  "background": { "service_worker": "background.js" }
}
```

```js
// background.js
chrome.action.onClicked.addListener(tab => {
  chrome.sidePanel.open({ tabId: tab.id });
});
```

The side panel is always-on — it persists across page navigations within a tab and does not close when the user switches pages.

---

## Job State — `chrome.storage.local`

All job state is stored under the key `"ttjobs"` in `chrome.storage.local` as an array of job objects:

```js
{
  job_id:       "uuid-v4",
  company:      "Google",
  resume_name:  "resumes/master_resume.tex",
  method:       "gemini" | "claudecli",
  status:       "queued" | "running" | "completed" | "error",
  submitted_at: 1234567890,   // Date.now() at submission
  log:          []            // populated only at completion/error, empty during streaming
}
```

**Why `chrome.storage.local` (not in-memory):**
- State survives side panel close and reopen
- State survives extension reload (short of a full uninstall)
- Multiple side panels across tabs automatically stay in sync

### Storage Helpers

```js
function getJobs()         // → Promise<job[]>
function saveJobs(jobs)    // → Promise<void>
async function addJob(job) // appends to array and saves
async function updateJobStatus(job_id, updates)  // patches one job's fields
async function removeJob(job_id)                 // removes from array
```

---

## Cross-Tab Sync

```js
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes[STORAGE_KEY]) {
    renderQueue(changes[STORAGE_KEY].newValue || []);
  }
});
```

Any write to `chrome.storage.local` from any side panel fires `onChanged` in every other open side panel. `renderQueue()` is called with the new array — all panels reflect the same state without polling.

---

## SSE Connection Management

### Module-Level State

```js
const sseMap   = new Map();   // job_id → EventSource (prevents duplicate connections)
const logCache = new Map();   // job_id → string[]  (in-memory log buffer per session)
```

### `attachSSE(job_id)`

Opens a single `EventSource` per job, guarded by `sseMap.has(job_id)`. If a connection already exists, returns immediately.

```js
const es = new EventSource(`${API}/status/${job_id}`);
sseMap.set(job_id, es);
logCache.set(job_id, []);
```

**`es.onmessage`** — live log lines:
```js
es.onmessage = e => {
  logCache.get(job_id).push(e.data);
  // write directly to card's <pre> — no storage write, no renderQueue()
  const logsEl = card.querySelector('.job-card-logs');
  logsEl.textContent += (logsEl.textContent ? '\n' : '') + e.data;
};
```

Log lines are buffered in `logCache` (not storage) during streaming. This avoids O(n²) storage writes as the log grows.

**`es.addEventListener('completed', ...)`** — PDF ready:
```js
es.addEventListener('completed', async () => {
  completedReceived = true;   // set synchronously before any await
  es.close();
  sseMap.delete(job_id);
  const finalLog = logCache.get(job_id) || [];
  logCache.delete(job_id);
  await updateJobStatus(job_id, { status: 'completed', pdf_ready: true, log: finalLog });
});
```

The full log is written to storage exactly once, at completion.

**`completedReceived` flag:**
```js
let completedReceived = false;

es.onerror = async () => {
  if (!sseMap.has(job_id)) return;      // already closed cleanly
  if (completedReceived) {               // server closed stream after completed — not an error
    es.close(); sseMap.delete(job_id); logCache.delete(job_id);
    return;
  }
  // genuine connection failure — mark as error
  ...
};
```

When the server closes an SSE stream after emitting the `completed` event, browsers fire `onerror`. Without the flag, this would overwrite the completed job with `status: "error"`. Setting `completedReceived` synchronously in the `completed` handler (before any `await`) prevents this race.

---

## DOM Reconciler (`renderQueue`)

`renderQueue(jobs)` is a reconciler, not a full rebuild:

```js
function renderQueue(jobs) {
  updateSlotCounter(jobs);
  const sorted = [...jobs].reverse();   // newest first

  const orderedCards = sorted.map(job => {
    const existing = list.querySelector(`.job-card[data-job-id="${job.job_id}"]`);
    if (existing) {
      patchJobCard(existing, job);   // update in-place — event listeners preserved
      return existing;
    }
    return createJobCard(job);       // create new card
  });

  // Remove cards for discarded jobs
  const currentIds = new Set(jobs.map(j => j.job_id));
  list.querySelectorAll('.job-card').forEach(card => {
    if (!currentIds.has(card.dataset.jobId)) card.remove();
  });

  // Re-append in sorted order (appendChild moves existing nodes — no cloning)
  orderedCards.forEach(card => list.appendChild(card));

  // Start SSE for any active job without a live connection in this tab
  for (const job of jobs) {
    if ((job.status === 'queued' || job.status === 'running') && !sseMap.has(job.job_id)) {
      attachSSE(job.job_id);
    }
  }
}
```

`patchJobCard(card, job)` updates only the status badge text/class and rebuilds the actions row if the status changed (e.g. `error → completed` after recompile). It never resets `innerHTML` so button event listeners attached at card-creation time are preserved.

### Actions Row Rebuild Logic

The actions row (`createActionsDiv`) differs between `completed` and `error`:
- `completed`: Open PDF + View Details + Recompile + Delete
- `error`: Recompile + Delete (no Open PDF or View Details)

`patchJobCard` detects the transition by checking whether the existing actions div has a `.job-open-btn`:

```js
if (job.status === 'completed') {
  const existing = card.querySelector('.job-actions');
  if (!existing || !existing.querySelector('.job-open-btn')) {
    if (existing) existing.remove();
    body.appendChild(createActionsDiv(job, card));
  }
}
```

---

## JD Auto-Extraction (`extractFromPage`)

On `DOMContentLoaded`, the extension runs `chrome.scripting.executeScript` on the active tab to scrape JD content and company name. The injected function tries a priority-ordered list of CSS selectors for each job board:

**JD selectors (in order):**
1. LinkedIn — `.jobs-description__content .jobs-box__html-content`
2. LinkedIn — `.jobs-description-content__text`
3. Indeed — `#jobDescriptionText`
4. Greenhouse — `#content .job__description`
5. Lever — `.posting-page .posting-description`
6. Workday — `[data-automation-id="job-description"]`
7. Generic — `[class*="jobDescriptionContent"]`, `[class*="job-description"]`, `[id*="job-description"]`

**Company selectors (in order):**
1. LinkedIn — `.jobs-unified-top-card__company-name a`
2. LinkedIn — `[data-testid="inlineHeader-companyName"] a`
3. Indeed — `.company-name`
4. Workday — `[data-automation-id="company"]`
5. Generic — `[class*="employerName"]`
6. Fallback — `document.title` regex: ` at {Company}` pattern

The first selector that returns an element with content wins. Extracted values are pre-filled into the form fields.

---

## Output Browser

Accessible via the "Browse Output" button. Displays all resumes in `output/` that have both `.tex` and `.pdf` present (from `GET /output/resumes`).

### Four-Panel Navigation Model

The extension has four mutually exclusive views, toggled by showing/hiding sections:
1. **Main** — form + queue (`form-section` + `queue-section`)
2. **Details** — structured resume data (`details-section`)
3. **Output Browser** — archived resumes (`output-section`)
4. **Details from Output** — details with output-browser as return destination

`detailsReturnTo = 'main' | 'output'` controls where "Back" navigates.

### Archived Resume Endpoints

Archived resumes have no live `job_id` in the server's `jobs` dict. All requests use `job_id='_'`:

```js
// Open PDF for archived resume
fetch(`${API}/open/_?company=${encodeURIComponent(company)}`)

// View Details for archived resume
showDetails({ job_id: '_', company }, 'output')

// Recompile archived resume
fetch(`${API}/recompile/_?company=${encodeURIComponent(company)}`, { method: 'POST' })

// Delete archived resume
fetch(`${API}/files/_?company=${encodeURIComponent(company)}`, { method: 'DELETE' })
```

`job_id='_'` is not found in the server's `jobs` dict, which triggers the `company` query param fallback path on all four endpoints — reconstructing file paths as `output/{company}_Resume.{ext}`.

---

## Slot Counter and Generate Button State

```js
function updateSlotCounter(jobs) {
  const activeCount = jobs.filter(j => j.status === 'queued' || j.status === 'running').length;
  $('slot-counter').textContent = `${activeCount} / ${MAX_SLOTS} slots used`;
  const btn = $('generate-btn');
  btn.disabled = activeCount >= MAX_SLOTS || $('api-status').className === 'offline';
}
```

The generate button is disabled when:
- 5 slots are in use (server would return 429)
- The API health check failed at startup (server is offline)

---

## Startup Sequence (`DOMContentLoaded`)

```js
document.addEventListener('DOMContentLoaded', async () => {
  const online = await checkAPI();     // GET /health with 3s timeout
  await loadResumes();                 // GET /resumes → populate dropdown
  await loadLocations();              // GET /locations → populate dropdown

  const { jd, company } = await extractFromPage();  // scrape active tab
  if (jd)      $('jd').value           = jd;
  if (company) $('company-name').value = company;

  $('generate-btn').disabled = !online;
  // attach button listeners ...

  const jobs = await getJobs();        // read chrome.storage.local
  renderQueue(jobs);                   // render all persisted jobs

  chrome.storage.onChanged.addListener(...);  // enable cross-tab sync
});
```
