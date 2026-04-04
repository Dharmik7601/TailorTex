'use strict';

// ── MockEventSource ───────────────────────────────────────────────────────────
// A controllable stand-in for the browser's EventSource.
// Tests drive it by calling _fireMessage(), _fireNamedEvent(), _fireError().
class MockEventSource {
  constructor(url) {
    this.url = url;
    this.onmessage = null;
    this.onerror = null;
    this.closed = false;
    this._listeners = {};
    MockEventSource.instances.push(this);
  }
  addEventListener(event, handler) {
    if (!this._listeners[event]) this._listeners[event] = [];
    this._listeners[event].push(handler);
  }
  close() { this.closed = true; }

  // Test helpers
  _fireMessage(data) { if (this.onmessage) this.onmessage({ data }); }
  _fireNamedEvent(event, data = event) {
    (this._listeners[event] || []).forEach(h => h({ data }));
  }
  _fireError(data = null) { if (this.onerror) this.onerror({ data }); }
}
MockEventSource.instances = [];

// ── Minimal DOM structure matching popup.html ─────────────────────────────────
function buildDOM() {
  document.body.innerHTML = `
    <header>
      <span id="api-status" class="online">Online</span>
    </header>
    <div id="form-section">
      <input id="company-name" type="text">
      <textarea id="jd"></textarea>
      <select id="resume-select"><option value="resumes/mr.tex">resumes/mr.tex</option></select>
      <select id="method-select">
        <option value="gemini">Gemini</option>
        <option value="claudecli">Claude</option>
      </select>
      <input type="checkbox" id="use-constraints" checked>
      <input type="checkbox" id="use-projects" checked>
      <button id="generate-btn" disabled>Generate Resume</button>
    </div>
    <div id="queue-section">
      <span id="slot-counter">0 / 5 slots used</span>
      <div id="job-list"></div>
    </div>
  `;
}

// ── Functional chrome.storage mock ────────────────────────────────────────────
// Returns the real stored data so getJobs/saveJobs behave correctly.
function makeChromeStorageMock() {
  const store = {};
  return {
    get: jest.fn((keys, callback) => {
      const result = {};
      (Array.isArray(keys) ? keys : [keys]).forEach(k => {
        if (store[k] !== undefined) result[k] = store[k];
      });
      callback(result);
    }),
    set: jest.fn((data, callback) => {
      Object.assign(store, data);
      if (callback) callback();
    }),
    _store: store,
  };
}

// ── Module under test ─────────────────────────────────────────────────────────
// Required here — after global chrome stub is set in jest.setup.js.
const popup = require('../popup.js');

// ─────────────────────────────────────────────────────────────────────────────
//  Helpers
// ─────────────────────────────────────────────────────────────────────────────
function makeJob(overrides = {}) {
  return {
    job_id: 'job-1',
    company: 'Acme',
    resume_name: 'resumes/master_resume.tex',
    method: 'gemini',
    status: 'queued',
    submitted_at: 1000,
    log: [],
    ...overrides,
  };
}

function resetMaps() {
  popup.sseMap.clear();
  popup.logCache.clear();
}

// ─────────────────────────────────────────────────────────────────────────────
//  Suite 1 — Storage write frequency  (Issue 2)
// ─────────────────────────────────────────────────────────────────────────────
describe('Issue 2 — storage write frequency', () => {
  let storageMock;

  beforeEach(() => {
    buildDOM();
    resetMaps();
    MockEventSource.instances = [];
    global.EventSource = jest.fn(url => new MockEventSource(url));

    storageMock = makeChromeStorageMock();
    global.chrome.storage.local = storageMock;

    // Seed one queued job in storage
    storageMock._store['ttjobs'] = [makeJob()];
  });

  test('zero storage writes while SSE log messages arrive', async () => {
    popup.attachSSE('job-1');
    const es = MockEventSource.instances[0];

    // Simulate 10 log lines streaming in
    for (let i = 0; i < 10; i++) es._fireMessage(`log line ${i}`);

    // No storage writes should have occurred
    expect(storageMock.set).not.toHaveBeenCalled();
  });

  test('exactly one storage write on job completion, containing the full log', async () => {
    popup.attachSSE('job-1');
    const es = MockEventSource.instances[0];

    es._fireMessage('line A');
    es._fireMessage('line B');
    es._fireMessage('line C');

    // Still no writes mid-stream
    expect(storageMock.set).not.toHaveBeenCalled();

    es._fireNamedEvent('completed');

    // Allow the async updateJobStatus chain to resolve
    await Promise.resolve();

    expect(storageMock.set).toHaveBeenCalledTimes(1);

    const writtenJobs = storageMock.set.mock.calls[0][0]['ttjobs'];
    const writtenJob = writtenJobs.find(j => j.job_id === 'job-1');

    expect(writtenJob.status).toBe('completed');
    expect(writtenJob.log).toEqual(['line A', 'line B', 'line C']);
  });

  test('exactly one storage write on error event, containing accumulated log', async () => {
    popup.attachSSE('job-1');
    const es = MockEventSource.instances[0];

    es._fireMessage('starting...');
    es._fireMessage('something failed');

    es._fireNamedEvent('error', 'error');

    await Promise.resolve();

    expect(storageMock.set).toHaveBeenCalledTimes(1);

    const writtenJobs = storageMock.set.mock.calls[0][0]['ttjobs'];
    const writtenJob = writtenJobs.find(j => j.job_id === 'job-1');

    expect(writtenJob.status).toBe('error');
    expect(writtenJob.log).toEqual(['starting...', 'something failed']);
  });

  test('logCache is cleared after job reaches terminal state', async () => {
    popup.attachSSE('job-1');
    const es = MockEventSource.instances[0];

    es._fireMessage('line 1');
    expect(popup.logCache.has('job-1')).toBe(true);

    es._fireNamedEvent('completed');
    await Promise.resolve();

    expect(popup.logCache.has('job-1')).toBe(false);
  });

  test('sseMap entry is removed immediately on completed event', async () => {
    popup.attachSSE('job-1');
    expect(popup.sseMap.has('job-1')).toBe(true);

    const es = MockEventSource.instances[0];
    es._fireNamedEvent('completed');
    await Promise.resolve();

    expect(popup.sseMap.has('job-1')).toBe(false);
    expect(es.closed).toBe(true);
  });

});

// ─────────────────────────────────────────────────────────────────────────────
//  Suite 2 — DOM reconciliation  (Issue 1)
// ─────────────────────────────────────────────────────────────────────────────
describe('Issue 1 — DOM reconciliation', () => {
  let storageMock;

  beforeEach(() => {
    buildDOM();
    resetMaps();
    MockEventSource.instances = [];
    global.EventSource = jest.fn(url => new MockEventSource(url));

    storageMock = makeChromeStorageMock();
    global.chrome.storage.local = storageMock;
  });

  test('existing card element is reused (not recreated) on re-render', () => {
    const job = makeJob({ status: 'running' });
    popup.renderQueue([job]);

    const cardBefore = document.querySelector('[data-job-id="job-1"]');
    expect(cardBefore).not.toBeNull();

    // Simulate a storage-change-driven re-render (e.g. status update)
    popup.renderQueue([{ ...job, status: 'completed', log: ['done'] }]);

    const cardAfter = document.querySelector('[data-job-id="job-1"]');

    // Same DOM node — not rebuilt
    expect(cardAfter).toBe(cardBefore);
  });

  test('status badge is updated in-place on re-render', () => {
    const job = makeJob({ status: 'running' });
    popup.renderQueue([job]);

    popup.renderQueue([{ ...job, status: 'completed' }]);

    const badge = document.querySelector('[data-job-id="job-1"] .status-badge');
    expect(badge.textContent).toBe('completed');
    expect(badge.className).toContain('status-completed');
  });

  test('Open PDF button is added when job transitions to completed', () => {
    const job = makeJob({ status: 'running' });
    popup.renderQueue([job]);

    expect(document.querySelector('[data-job-id="job-1"] .job-open-btn')).toBeNull();

    popup.renderQueue([{ ...job, status: 'completed' }]);

    expect(document.querySelector('[data-job-id="job-1"] .job-open-btn')).not.toBeNull();
  });

  test('discarded job card is removed from DOM', () => {
    const job1 = makeJob({ job_id: 'job-1', company: 'Acme' });
    const job2 = makeJob({ job_id: 'job-2', company: 'Globex' });

    popup.renderQueue([job1, job2]);
    expect(document.querySelectorAll('.job-card').length).toBe(2);

    popup.renderQueue([job1]); // job2 removed
    expect(document.querySelectorAll('.job-card').length).toBe(1);
    expect(document.querySelector('[data-job-id="job-2"]')).toBeNull();
  });

  test('new job card is inserted without destroying existing cards', () => {
    const job1 = makeJob({ job_id: 'job-1', status: 'completed' });
    popup.renderQueue([job1]);

    const cardBefore = document.querySelector('[data-job-id="job-1"]');

    const job2 = makeJob({ job_id: 'job-2', status: 'running' });
    popup.renderQueue([job1, job2]);

    // Original card still in DOM and is the same node
    expect(document.querySelector('[data-job-id="job-1"]')).toBe(cardBefore);
    // New card also present
    expect(document.querySelector('[data-job-id="job-2"]')).not.toBeNull();
  });

  test('newest job appears first in the DOM (newest-first order)', () => {
    const job1 = makeJob({ job_id: 'job-1', submitted_at: 1000 });
    const job2 = makeJob({ job_id: 'job-2', submitted_at: 2000 });
    const job3 = makeJob({ job_id: 'job-3', submitted_at: 3000 });

    // jobs array is oldest-first (submission order)
    popup.renderQueue([job1, job2, job3]);

    const cards = document.querySelectorAll('.job-card');
    expect(cards[0].dataset.jobId).toBe('job-3'); // newest at top
    expect(cards[2].dataset.jobId).toBe('job-1'); // oldest at bottom
  });

  test('slot counter reflects only active (queued/running) jobs', () => {
    popup.renderQueue([
      makeJob({ job_id: 'j1', status: 'completed' }),
      makeJob({ job_id: 'j2', status: 'running' }),
      makeJob({ job_id: 'j3', status: 'queued' }),
      makeJob({ job_id: 'j4', status: 'error' }),
    ]);

    expect(document.getElementById('slot-counter').textContent).toBe('2 / 5 slots used');
  });

  test('generate button is disabled when 5 active slots are used', () => {
    const jobs = Array.from({ length: 5 }, (_, i) =>
      makeJob({ job_id: `j${i}`, status: 'running' })
    );
    popup.renderQueue(jobs);

    expect(document.getElementById('generate-btn').disabled).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
//  Suite 3 — Live log update path
// ─────────────────────────────────────────────────────────────────────────────
describe('Live log update path', () => {
  let storageMock;

  beforeEach(() => {
    buildDOM();
    resetMaps();
    MockEventSource.instances = [];
    global.EventSource = jest.fn(url => new MockEventSource(url));

    storageMock = makeChromeStorageMock();
    global.chrome.storage.local = storageMock;
    storageMock._store['ttjobs'] = [makeJob({ status: 'running' })];
  });

  test('SSE log lines are written directly to the card log element', () => {
    popup.renderQueue([makeJob({ status: 'running' })]);
    popup.attachSSE('job-1');

    const logsEl = document.querySelector('[data-job-id="job-1"] .job-card-logs');
    expect(logsEl.textContent).toBe('');

    const es = MockEventSource.instances[0];
    es._fireMessage('first line');
    es._fireMessage('second line');

    expect(logsEl.textContent).toBe('first line\nsecond line');
  });

  test('SSE log lines do NOT trigger any storage write', () => {
    popup.renderQueue([makeJob({ status: 'running' })]);
    popup.attachSSE('job-1');

    const es = MockEventSource.instances[0];
    for (let i = 0; i < 20; i++) es._fireMessage(`line ${i}`);

    expect(storageMock.set).not.toHaveBeenCalled();
  });

  test('log element is NOT overwritten by patchJobCard when SSE is active', () => {
    popup.renderQueue([makeJob({ status: 'running' })]);
    popup.attachSSE('job-1');

    const es = MockEventSource.instances[0];
    es._fireMessage('live line 1');
    es._fireMessage('live line 2');

    // Simulate a storage-change re-render arriving (e.g. from another tab)
    // with a stale or partial log — should not overwrite live content
    popup.renderQueue([makeJob({ status: 'running', log: ['stale log'] })]);

    const logsEl = document.querySelector('[data-job-id="job-1"] .job-card-logs');
    expect(logsEl.textContent).toBe('live line 1\nlive line 2');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
//  Suite 4 — Cross-tab / panel-reopen behaviour
// ─────────────────────────────────────────────────────────────────────────────
describe('Cross-tab sync', () => {
  beforeEach(() => {
    buildDOM();
    resetMaps();
    MockEventSource.instances = [];
    global.EventSource = jest.fn(url => new MockEventSource(url));
    global.chrome.storage.local = makeChromeStorageMock();
  });

  test('log from storage is shown when card is created for a completed job', () => {
    const job = makeJob({ status: 'completed', log: ['line 1', 'line 2', 'done'] });
    popup.renderQueue([job]);

    const logsEl = document.querySelector('[data-job-id="job-1"] .job-card-logs');
    expect(logsEl.textContent).toBe('line 1\nline 2\ndone');
  });

  test('patchJobCard updates log from storage when no SSE is active in this tab', () => {
    // Simulate the second-tab scenario by creating a card directly (no renderQueue,
    // which would auto-start SSE) and then calling patchJobCard in isolation.
    const job = makeJob({ status: 'running', log: [] });
    const card = popup.createJobCard(job);
    document.getElementById('job-list').appendChild(card);

    // Confirm no SSE is active for this job in this tab
    expect(popup.sseMap.has('job-1')).toBe(false);

    // Completion arrives via storage change (cross-tab) — patchJobCard should
    // write the stored final log into the log element
    popup.patchJobCard(card, { ...job, status: 'completed', log: ['line 1', 'line 2'] });

    const logsEl = card.querySelector('.job-card-logs');
    expect(logsEl.textContent).toBe('line 1\nline 2');
  });

  test('attachSSE deduplication — second call for same job_id is a no-op', () => {
    global.chrome.storage.local._store = {};
    global.chrome.storage.local._store['ttjobs'] = [makeJob()];

    popup.attachSSE('job-1');
    popup.attachSSE('job-1'); // second call
    popup.attachSSE('job-1'); // third call

    // EventSource constructor called only once
    expect(global.EventSource).toHaveBeenCalledTimes(1);
  });
});
