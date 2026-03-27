/**
 * batch.js — Batch queue: parse multi-line prompts, run sequentially with delay.
 */

import { submitGeneration, saveHistoryEntry } from './api.js';
import { recordHistory } from './history.js';

let _jobs = [];
let _isRunning = false;
let _isPaused = false;

// ── Init ──────────────────────────────────────────────────────────────────────

export function initBatch(models) {
  _populateModelSelect(models);

  document.getElementById('batchDelaySlider').addEventListener('input', _syncDelayLabel);
  document.getElementById('batchPromptsInput').addEventListener('input', _updatePromptCount);
  document.getElementById('batchSampleCount')?.addEventListener('input', () => {
    document.getElementById('batchSampleVal').textContent = document.getElementById('batchSampleCount').value;
  });
  document.getElementById('batchParseBtn').addEventListener('click', parseBatch);
  document.getElementById('batchStartBtn').addEventListener('click', startBatch);
  document.getElementById('batchPauseBtn').addEventListener('click', pauseBatch);
  document.getElementById('batchStopBtn').addEventListener('click', stopBatch);
  document.getElementById('batchLogClear').addEventListener('click', () => {
    document.getElementById('batchLogBody').innerHTML = '';
  });

  _syncDelayLabel();
  _updateStats();
  _log('info', 'Ready. Paste prompts (one per line) and click Parse.');
}

function _populateModelSelect(models) {
  const sel = document.getElementById('batchModel');
  sel.innerHTML = '';
  models.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.model_id;
    opt.textContent = m.display_name;
    sel.appendChild(opt);
  });
}

function _syncDelayLabel() {
  const v = document.getElementById('batchDelaySlider').value;
  document.getElementById('batchDelayVal').textContent = v + 's';
}

function _updatePromptCount() {
  const lines = document.getElementById('batchPromptsInput').value.split('\n').filter(l => l.trim());
  document.getElementById('batchPromptCount').textContent = lines.length;
}

// ── Parse ─────────────────────────────────────────────────────────────────────

export function parseBatch() {
  const text = document.getElementById('batchPromptsInput').value.trim();
  const lines = text.split('\n').filter(l => l.trim());
  if (!lines.length) {
    _log('warn', 'No prompts detected — enter one prompt per line.');
    return;
  }

  if (_isRunning) {
    _log('warn', 'Stop the queue before re-parsing.');
    return;
  }

  _jobs = lines.map((p, i) => ({
    id: i + 1,
    prompt: p.trim(),
    status: 'waiting',
    startTime: null,
    elapsed: null,
    error: null,
  }));

  _renderAllJobs();
  _updateStats();
  document.getElementById('batchStartBtn').disabled = false;
  _log('ok', `Parsed ${_jobs.length} prompts. Press Start to begin.`);
}

// ── Render ────────────────────────────────────────────────────────────────────

function _renderAllJobs() {
  const list = document.getElementById('batchJobList');
  // Remove only job cards, keep the empty-state placeholder
  list.querySelectorAll('[id^="bjob-"]').forEach(el => el.remove());
  document.getElementById('batchEmpty')?.classList.add('hidden');
  _jobs.forEach(j => _renderJob(j, true));
}

function _isSafetyError(msg) {
  return /safety filter|raiMedia|blocked by|content policy/i.test(msg ?? '');
}

function _renderJob(job, append = false) {
  const existing = document.getElementById('bjob-' + job.id);
  const card = existing ?? document.createElement('div');
  card.id = 'bjob-' + job.id;

  const isSafety = job.status === 'error' && _isSafetyError(job.error);

  const S = {
    waiting: { badge: 'WAIT',    badgeCls: 'bg-gray-800 text-gray-500',           cardCls: 'opacity-60' },
    running: { badge: 'GEN…',    badgeCls: 'bg-emerald-900/50 text-emerald-400',  cardCls: 'border-emerald-500/40 bg-emerald-950/20' },
    delay:   { badge: 'DELAY',   badgeCls: 'bg-yellow-900/50 text-yellow-400',    cardCls: 'border-yellow-500/40' },
    done:    { badge: 'DONE',    badgeCls: 'bg-blue-900/50 text-blue-400',        cardCls: 'border-blue-500/30 bg-blue-950/10' },
    error:   isSafety
      ? { badge: 'FILTERED', badgeCls: 'bg-orange-900/50 text-orange-400',    cardCls: 'border-orange-500/30 bg-orange-950/10' }
      : { badge: 'ERROR',    badgeCls: 'bg-red-900/50 text-red-400',          cardCls: 'border-red-500/30 bg-red-950/20' },
  };
  const s = S[job.status] ?? S.waiting;

  const shortPrompt = job.prompt.length > 90 ? job.prompt.slice(0, 90) + '…' : job.prompt;
  const elapsed = job.elapsed != null ? `${job.elapsed}s` : '';

  const errorColor = isSafety ? 'text-orange-400' : 'text-red-400';
  const errorLabel = isSafety ? '⚠ Content safety filter blocked this prompt' : _esc(job.error);

  const progressBar = (job.status === 'running' || job.status === 'delay')
    ? `<div class="mt-2 h-px bg-gray-700 rounded-full overflow-hidden">
         <div id="bprog-${job.id}" class="h-full ${job.status === 'delay' ? 'bg-yellow-500' : 'bg-emerald-500'} rounded-full" style="width:0%"></div>
       </div>`
    : job.status === 'done'
    ? `<div class="mt-2 h-px bg-blue-500/40 rounded-full"></div>`
    : job.status === 'error'
    ? `<div class="mt-2 h-px ${isSafety ? 'bg-orange-500/40' : 'bg-red-500/40'} rounded-full"></div>`
    : '';

  card.className = `rounded-lg border border-gray-700/50 bg-gray-900 p-3 transition-all duration-200 ${s.cardCls}`;
  card.innerHTML = `
    <div class="flex items-start gap-3">
      <span class="font-mono text-[11px] text-gray-600 pt-0.5 w-7 text-right flex-shrink-0">#${String(job.id).padStart(2, '0')}</span>
      <div class="flex-1 min-w-0">
        <p class="text-xs text-gray-200 leading-relaxed break-words" title="${_esc(job.prompt)}">${shortPrompt}</p>
        ${job.error ? `<p class="font-mono text-[11px] ${errorColor} mt-1 break-words">${errorLabel}</p>` : ''}
      </div>
      <div class="flex flex-col items-end gap-1 flex-shrink-0 ml-2">
        <span class="font-mono text-[10px] px-1.5 py-0.5 rounded ${s.badgeCls}">${s.badge}</span>
        ${elapsed ? `<span class="font-mono text-[10px] text-gray-600">${elapsed}</span>` : ''}
      </div>
    </div>
    ${progressBar}
  `;

  if (append && !existing) {
    document.getElementById('batchJobList').appendChild(card);
  }
}

function _esc(str) {
  return (str ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Stats ─────────────────────────────────────────────────────────────────────

function _updateStats() {
  const wait = _jobs.filter(j => j.status === 'waiting').length;
  const run  = _jobs.filter(j => j.status === 'running' || j.status === 'delay').length;
  const done = _jobs.filter(j => j.status === 'done').length;
  const err  = _jobs.filter(j => j.status === 'error').length;

  document.getElementById('bStatWait').textContent = wait;
  document.getElementById('bStatRun').textContent  = run;
  document.getElementById('bStatDone').textContent = done;
  document.getElementById('bStatErr').textContent  = err;
  document.getElementById('bStatTotal').textContent = _jobs.length;

  const dot = document.getElementById('bRunDot');
  dot?.classList.toggle('animate-pulse', run > 0);
  dot?.classList.toggle('opacity-0', run === 0);
}

// ── Log ───────────────────────────────────────────────────────────────────────

function _log(type, msg) {
  const body = document.getElementById('batchLogBody');
  const now = new Date().toTimeString().slice(0, 8);
  const color = { ok: 'text-emerald-400', warn: 'text-yellow-400', err: 'text-red-400', info: 'text-gray-400' }[type] ?? 'text-gray-400';
  const line = document.createElement('div');
  line.className = 'flex gap-3 leading-relaxed';
  line.innerHTML = `<span class="font-mono text-[11px] text-gray-600 flex-shrink-0">${now}</span><span class="font-mono text-[11px] ${color}">${_esc(msg)}</span>`;
  body.appendChild(line);
  body.scrollTop = body.scrollHeight;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function _animateProgress(jobId, durationMs) {
  const start = Date.now();
  const tick = () => {
    const el = document.getElementById('bprog-' + jobId);
    if (!el) return;
    const pct = Math.min(99, ((Date.now() - start) / durationMs) * 100);
    el.style.width = pct + '%';
    if (pct < 99) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

// ── Run single job ────────────────────────────────────────────────────────────

async function _runJob(job) {
  job.status = 'running';
  job.startTime = Date.now();
  _renderJob(job);
  _updateStats();

  const model        = document.getElementById('batchModel').value;
  const duration     = parseInt(document.querySelector('input[name="batchDurationRadio"]:checked')?.value ?? '6', 10);
  const aspectRatio  = document.querySelector('input[name="batchAspectRatio"]:checked')?.value ?? '16:9';
  const resolution   = document.querySelector('input[name="batchResolution"]:checked')?.value ?? '720p';
  const generateAudio = document.getElementById('batchGenerateAudio').checked;
  const sampleCount  = parseInt(document.getElementById('batchSampleCount')?.value ?? '1', 10);
  const seedRaw      = document.getElementById('batchSeed')?.value.trim();
  const seed         = seedRaw ? parseInt(seedRaw, 10) : null;
  const storageUri   = document.getElementById('batchStorageUri')?.value.trim() || null;
  const negPrompt    = document.getElementById('batchNegPrompt')?.value.trim() || null;

  _animateProgress(job.id, duration * 1000 * 12); // rough estimate: ~12x realtime
  _log('info', `[#${job.id}] Submitting: ${job.prompt.slice(0, 60)}${job.prompt.length > 60 ? '…' : ''}`);

  try {
    const { ok, data } = await submitGeneration({
      task: 'text_to_video',
      prompt: job.prompt,
      model,
      duration,
      config: {
        aspect_ratio:    aspectRatio,
        sample_count:    sampleCount,
        generate_audio:  generateAudio,
        resolution,
        seed,
        storage_uri:     storageUri,
        negative_prompt: negPrompt,
      },
    });

    if (!ok) throw new Error(data.detail ?? JSON.stringify(data));

    const filename = data.file_path.split('/').pop();
    recordHistory(filename, { prompt: job.prompt, model, task: 'text_to_video', duration, aspectRatio });
    saveHistoryEntry({ filename, prompt: job.prompt, model, task: 'text_to_video', duration, aspect_ratio: aspectRatio }).catch(() => {});

    job.status = 'done';
    job.elapsed = Math.round((Date.now() - job.startTime) / 1000);
    _renderJob(job);
    _log('ok', `[#${job.id}] Done → ${filename} (${job.elapsed}s)`);
  } catch (err) {
    job.status = 'error';
    job.error = err.message;
    job.elapsed = Math.round((Date.now() - job.startTime) / 1000);
    _renderJob(job);
    _log('err', `[#${job.id}] Error: ${err.message}`);
  }

  _updateStats();
}

// ── Queue controls ────────────────────────────────────────────────────────────

export async function startBatch() {
  if (!_jobs.length) { _log('warn', 'Parse prompts first.'); return; }
  if (_isRunning) return;

  // Reset all waiting jobs (allow re-run after stop)
  _jobs.filter(j => j.status !== 'done').forEach(j => { j.status = 'waiting'; _renderJob(j); });
  _updateStats();

  _isRunning = true;
  _isPaused = false;

  document.getElementById('batchStartBtn').disabled = true;
  document.getElementById('batchPauseBtn').disabled = false;
  document.getElementById('batchStopBtn').disabled = false;
  document.getElementById('batchParseBtn').disabled = true;

  const delaySec = parseInt(document.getElementById('batchDelaySlider').value, 10);
  const delayMs  = delaySec * 1000;
  _log('ok', `Queue started — ${_jobs.length} jobs, ${delaySec}s delay between each.`);

  for (let i = 0; i < _jobs.length; i++) {
    if (!_isRunning) break;

    const job = _jobs[i];
    if (job.status === 'done') continue;  // skip already-done (partial re-run)

    // Unpause gate
    while (_isPaused && _isRunning) await _sleep(300);
    if (!_isRunning) break;

    // Inter-job delay (skip before first job)
    if (i > 0 && delayMs > 0) {
      job.status = 'delay';
      _renderJob(job);
      _updateStats();
      _log('warn', `[#${job.id}] Waiting ${delaySec}s…`);
      _animateProgress(job.id, delayMs);

      const end = Date.now() + delayMs;
      while (Date.now() < end && _isRunning) {
        while (_isPaused && _isRunning) await _sleep(300);
        await _sleep(200);
      }
      if (!_isRunning) break;

      job.status = 'waiting';
    }

    await _runJob(job);
  }

  if (_isRunning) {
    _isRunning = false;
    const done     = _jobs.filter(j => j.status === 'done').length;
    const err      = _jobs.filter(j => j.status === 'error' && !_isSafetyError(j.error)).length;
    const filtered = _jobs.filter(j => j.status === 'error' && _isSafetyError(j.error)).length;
    _log('ok', `Queue complete — Done: ${done}${err ? `, Errors: ${err}` : ''}${filtered ? `, Safety filtered: ${filtered}` : ''}`);
  }

  _isRunning = false;
  document.getElementById('batchStartBtn').disabled = false;
  document.getElementById('batchPauseBtn').disabled = true;
  document.getElementById('batchPauseBtn').textContent = '⏸ Pause';
  document.getElementById('batchStopBtn').disabled = true;
  document.getElementById('batchParseBtn').disabled = false;
  _updateStats();
}

export function pauseBatch() {
  _isPaused = !_isPaused;
  document.getElementById('batchPauseBtn').textContent = _isPaused ? '▶ Resume' : '⏸ Pause';
  _log('warn', _isPaused ? 'Queue paused.' : 'Queue resumed.');
}

export function stopBatch() {
  _isRunning = false;
  _isPaused  = false;
  _jobs.filter(j => j.status === 'waiting' || j.status === 'delay').forEach(j => {
    j.status = 'waiting';
    _renderJob(j);
  });
  document.getElementById('batchStartBtn').disabled = _jobs.length === 0;
  document.getElementById('batchPauseBtn').disabled = true;
  document.getElementById('batchPauseBtn').textContent = '⏸ Pause';
  document.getElementById('batchStopBtn').disabled = true;
  document.getElementById('batchParseBtn').disabled = false;
  _log('err', 'Queue stopped.');
  _updateStats();
}
