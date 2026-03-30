/**
 * app.js — entry point. Boots the UI, wires up all modules.
 */

import { fetchModels, fetchHistory } from './api.js';
import { initTaskSelector } from './tasks.js';
import { initModels } from './models.js';
import { initPriceEstimate, handleSubmit } from './form.js';
import { renderHistory } from './history.js';
import { renderSettings, loadSettings, applyTheme } from './settings.js';
import { initBatch } from './batch.js';
import { initWhatIf } from './whatif.js';

// ── Navigation ─────────────────────────────────────────────────────────────

const VIEWS = ['generate', 'history', 'settings', 'batch', 'whatif'];

function switchView(target) {
  VIEWS.forEach(v => {
    const el = document.getElementById(`view-${v}`);
    if (el) {
      el.classList.toggle('hidden', v !== target);
      el.classList.toggle('flex', v === target);
    }
  });
  document.querySelectorAll('.nav-btn').forEach(btn => {
    const active = btn.dataset.view === target;
    btn.classList.toggle('bg-brand/10', active);
    btn.classList.toggle('text-brand', active);
    btn.classList.toggle('text-gray-400', !active);
  });

  // Toggle right-panel between preview, batch queue, and whatif
  const isWhatIf = target === 'whatif';
  const isBatch  = target === 'batch';
  document.getElementById('normalPreviewPanel')?.classList.toggle('hidden', isBatch || isWhatIf);
  document.getElementById('normalPreviewPanel')?.classList.toggle('flex', !isBatch && !isWhatIf);
  document.getElementById('batchQueuePanel')?.classList.toggle('hidden', !isBatch);
  document.getElementById('batchQueuePanel')?.classList.toggle('flex', isBatch);
  document.getElementById('whatifPanel')?.classList.toggle('hidden', !isWhatIf);
  document.getElementById('whatifPanel')?.classList.toggle('flex', isWhatIf);

  if (target === 'history') loadHistoryView();
  if (target === 'settings') renderSettings();
}

document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => switchView(btn.dataset.view));
});

// ── History helpers ─────────────────────────────────────────────────────────

async function loadHistoryView() {
  try {
    const { items } = await fetchHistory();
    renderHistory(items, onHistorySelect);
  } catch (err) {
    const list = document.getElementById('historyList');
    if (list) list.innerHTML = `<p class="text-xs text-red-400 text-center py-4">Failed to load: ${err.message}</p>`;
  }
}

function onHistorySelect(entry) {
  // Restore prompt + switch to generate view
  if (entry.prompt) document.getElementById('prompt').value = entry.prompt;
  if (entry.task) {
    const sel = document.getElementById('task');
    if (sel) { sel.value = entry.task; sel.dispatchEvent(new Event('change')); }
  }
  // Show video in preview
  if (entry.url) {
    const vid = document.getElementById('resultVideo');
    vid.src = entry.url;
    vid.load();
    vid.classList.remove('hidden');
    document.getElementById('placeholder')?.classList.add('hidden');
    const dl = document.getElementById('downloadBtn');
    dl.href = entry.url;
    dl.download = entry.filename ?? '';
    dl.classList.remove('hidden');
    const meta = [entry.task, entry.model, entry.duration ? `${entry.duration}s` : '', entry.aspectRatio].filter(Boolean).join(' · ');
    document.getElementById('resultMeta').textContent = meta;
    document.getElementById('previewMeta').textContent = meta;
  }
  switchView('generate');
}

document.getElementById('historyRefreshBtn')?.addEventListener('click', loadHistoryView);

// ── Boot ───────────────────────────────────────────────────────────────────

async function boot() {
  // Apply saved theme
  const { theme } = loadSettings();
  applyTheme(theme);

  // Task selector
  initTaskSelector();

  // Range display labels
  document.getElementById('sampleCount').addEventListener('input', () => {
    document.getElementById('sampleVal').textContent =
      document.getElementById('sampleCount').value;
  });
  document.getElementById('prompt').addEventListener('input', () => {
    document.getElementById('promptLen').textContent =
      document.getElementById('prompt').value.length;
  });

  // Models
  const badge = document.getElementById('locationBadge');
  try {
    const { models, default_model, current_location } = await fetchModels();
    badge.innerHTML =
      `<span class="w-2 h-2 rounded-full bg-green-500 inline-block"></span> ${current_location}`;
    initModels(models, default_model);
    initPriceEstimate();
    initBatch(models);
    initWhatIf(models);
  } catch (err) {
    badge.innerHTML =
      `<span class="w-2 h-2 rounded-full bg-red-500 inline-block"></span> error`;
    document.getElementById('modelList').innerHTML =
      `<p class="text-red-400 text-xs">Failed to load models: ${err.message}</p>`;
  }

  // Form
  document.getElementById('genForm').addEventListener('submit', handleSubmit);

  // Gen log clear button
  document.getElementById('genLogClear')?.addEventListener('click', () => {
    document.getElementById('genLogBody').innerHTML = '';
  });

  // WhatIf log clear button
  document.getElementById('wiLogClear')?.addEventListener('click', () => {
    document.getElementById('wiLogBody').innerHTML = '';
  });

  // Aspect ratio changes update draft box live
  document.querySelectorAll('input[name="aspectRatio"]').forEach(r => {
    r.addEventListener('change', () => _updateDraftBoxRatio(r.value));
  });
}

function _updateDraftBoxRatio(ratio) {
  const box = document.getElementById('draftBox');
  if (!box) return;
  const [w, h] = ratio.split(':').map(Number);
  box.style.aspectRatio = `${w}/${h}`;
  box.style.maxWidth = `calc((100vh - 11rem) * ${w / h})`;
}

boot();
