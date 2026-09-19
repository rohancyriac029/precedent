// ============================================================
// API Client — talks to the FastAPI backend at /api/*
// ============================================================

const BASE = '/api';

export async function getHealth() {
  const r = await fetch(`${BASE}/health`);
  if (!r.ok) throw new Error(`Health check failed: ${r.status}`);
  return r.json();
}

export async function runAudit({ input_type, content, graph }) {
  const body = {};
  if (graph) {
    body.graph = graph;
  } else {
    body.input_type = input_type;
    body.content = content;
  }
  const r = await fetch(`${BASE}/audits`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) {
    throw new Error(data.message || data.detail || `Audit failed: ${r.status}`);
  }
  return data;
}

export async function extractProse(content) {
  const r = await fetch(`${BASE}/extract`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input_type: 'prose', content }),
  });
  const data = await r.json();
  if (!r.ok) {
    throw new Error(data.message || data.detail || `Extraction failed: ${r.status}`);
  }
  return data; // has confirm_required: true
}

export async function getCorpusStats() {
  const r = await fetch(`${BASE}/corpus/stats`);
  if (!r.ok) throw new Error(`Stats failed: ${r.status}`);
  return r.json();
}
