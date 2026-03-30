/**
 * whatif.js — WhatIf Factory tab.
 * POST /whatif/start → SSE progress → video preview + brain output display.
 */

const VOICES = [
  { value: 'vi-VN-Neural2-A', label: 'Vietnamese Neural2 A' },
  { value: 'vi-VN-Neural2-D', label: 'Vietnamese Neural2 D' },
  { value: 'vi-VN-Standard-A', label: 'Vietnamese Standard A' },
  { value: 'vi-VN-Standard-D', label: 'Vietnamese Standard D' },
];

const DEFAULT_WHATIF_MODEL = 'veo-3.1-fast-generate-preview';
const WHATIF_CLIP_COUNT = 2;
const WHATIF_CLIP_SECONDS = 6;

let _modelsById = new Map();

export function initWhatIf(models) {
  _modelsById = new Map((models || []).map(m => [m.model_id, m]));

  // Populate model selector
  const modelSel = document.getElementById('wiModel');
  if (modelSel && models?.length) {
    modelSel.innerHTML = models
      .map(m => `<option value="${m.model_id}">${m.display_name}</option>`)
      .join('');
    const def = models.find(m => m.model_id === DEFAULT_WHATIF_MODEL) || models[0];
    if (def) modelSel.value = def.model_id;
    modelSel.addEventListener('change', _updatePriceEstimate);
    _updatePriceEstimate();
  }

  // Populate voice selector
  const voiceSel = document.getElementById('wiVoice');
  if (voiceSel) {
    voiceSel.innerHTML = VOICES.map(v =>
      `<option value="${v.value}"${v.value === 'vi-VN-Neural2-D' ? ' selected' : ''}>${v.label}</option>`
    ).join('');
  }

  document.getElementById('wiForm')?.addEventListener('submit', onSubmit);
}

async function onSubmit(e) {
  e.preventDefault();
  const topic = document.getElementById('wiTopic')?.value?.trim();
  if (!topic) return;

  const model = document.getElementById('wiModel')?.value || DEFAULT_WHATIF_MODEL;
  const voice = document.getElementById('wiVoice')?.value || 'vi-VN-Neural2-D';
  const lang  = document.getElementById('wiLang')?.value  || 'vi';

  _setRunning(true);
  _clearLog();
  _hideResult();
  _hideBrain();

  let jobId;
  try {
    const res = await fetch('/whatif/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic, model, voice_model: voice, language: lang }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    jobId = data.job_id;
    _log(`🚀 Job started: ${jobId}`, 'info');
  } catch (err) {
    _log(`❌ Failed to start: ${err.message}`, 'error');
    _setRunning(false);
    return;
  }

  // SSE progress
  await _streamEvents(jobId);
}

function _updatePriceEstimate() {
  const modelId = document.getElementById('wiModel')?.value;
  const el = document.getElementById('wiPriceEstimate');
  if (!el || !modelId) return;

  const m = _modelsById.get(modelId);
  const pps = Number(m?.price_per_second_usd || 0);
  if (!Number.isFinite(pps) || pps <= 0) {
    el.textContent = 'Estimated Veo cost: unavailable for this model';
    return;
  }

  const seconds = WHATIF_CLIP_COUNT * WHATIF_CLIP_SECONDS;
  const usd = (pps * seconds).toFixed(2);
  el.textContent = `Estimated Veo cost: ~$${usd} (${WHATIF_CLIP_COUNT}x${WHATIF_CLIP_SECONDS}s @ $${pps.toFixed(2)}/s)`;
}

function _streamEvents(jobId) {
  return new Promise(resolve => {
    _setProgressBar(0);

    // Use fetch + ReadableStream for SSE (avoids EventSource redirect issues)
    const ctrl = new AbortController();
    fetch(`/whatif/${jobId}/events`, { signal: ctrl.signal })
      .then(res => {
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';

        function pump() {
          reader.read().then(({ done, value }) => {
            if (done) { _setRunning(false); resolve(); return; }
            buf += dec.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop();
            lines.forEach(line => {
              if (!line.startsWith('data:')) return;
              try {
                const evt = JSON.parse(line.slice(5).trim());
                if (evt.ping) return;
                if (evt.done) {
                  _setProgressBar(100);
                  _loadResult(jobId);
                  _setRunning(false);
                  resolve();
                  return;
                }
                if (evt.failed) {
                  _log(`❌ ${evt.error || 'Pipeline failed'}`, 'error');
                  _setRunning(false);
                  _setProgressBar(0);
                  resolve();
                  return;
                }
                if (evt.message) {
                  _log(evt.message, 'info');
                  _setProgressBar(evt.percent ?? 0);
                }
              } catch {}
            });
            pump();
          }).catch(() => { _setRunning(false); resolve(); });
        }
        pump();
      })
      .catch(err => {
        _log(`❌ SSE error: ${err.message}`, 'error');
        _setRunning(false);
        resolve();
      });
  });
}

async function _loadResult(jobId) {
  try {
    const res = await fetch(`/whatif/${jobId}/result`);
    const data = await res.json();
    if (data.output_video) {
      _showVideo(data.output_video, data.duration_sec);
    }
    if (data.brain_output) {
      _showBrain(data.brain_output);
    }
  } catch (err) {
    _log(`⚠️ Could not load result: ${err.message}`, 'warn');
  }
}

// ── UI helpers ──────────────────────────────────────────────────────────────

function _setRunning(running) {
  const btn    = document.getElementById('wiSubmitBtn');
  const spin   = document.getElementById('wiSpinIcon');
  const label  = document.getElementById('wiSubmitLabel');
  if (!btn) return;
  btn.disabled = running;
  spin?.classList.toggle('hidden', !running);
  if (label) label.textContent = running ? 'Generating…' : 'Generate Shorts';
}

function _setProgressBar(pct) {
  const bar  = document.getElementById('wiProgressBar');
  const text = document.getElementById('wiProgressPct');
  if (bar)  bar.style.width  = `${pct}%`;
  if (text) text.textContent = `${pct}%`;
  document.getElementById('wiProgressWrap')?.classList.toggle('hidden', pct === 0);
}

function _log(msg, level = 'info') {
  const body = document.getElementById('wiLogBody');
  if (!body) return;
  const colors = { info: 'text-gray-300', error: 'text-red-400', warn: 'text-yellow-400' };
  const el = document.createElement('p');
  el.className = `font-mono text-[11px] leading-relaxed ${colors[level] || colors.info}`;
  el.textContent = msg;
  body.appendChild(el);
  body.scrollTop = body.scrollHeight;
}

function _clearLog() {
  const body = document.getElementById('wiLogBody');
  if (body) body.innerHTML = '';
  _setProgressBar(0);
  document.getElementById('wiProgressWrap')?.classList.add('hidden');
}

function _showVideo(url, duration) {
  const vid  = document.getElementById('wiResultVideo');
  const wrap = document.getElementById('wiVideoWrap');
  const dur  = document.getElementById('wiDuration');
  if (vid) { vid.src = url; vid.load(); }
  wrap?.classList.remove('hidden');
  if (dur && duration) dur.textContent = `${duration.toFixed(1)}s`;

  const dl = document.getElementById('wiDownloadBtn');
  if (dl) { dl.href = url; dl.download = url.split('/').pop(); dl.classList.remove('hidden'); }
}

function _hideResult() {
  document.getElementById('wiVideoWrap')?.classList.add('hidden');
  document.getElementById('wiDownloadBtn')?.classList.add('hidden');
}

function _showBrain(brain) {
  const wrap   = document.getElementById('wiBrainWrap');
  const script = document.getElementById('wiBrainScript');
  const vibe   = document.getElementById('wiBrainVibe');
  if (script) script.textContent = brain.script || '';
  if (vibe)   vibe.textContent   = brain.vibe   || '';
  wrap?.classList.remove('hidden');
}

function _hideBrain() {
  document.getElementById('wiBrainWrap')?.classList.add('hidden');
}
