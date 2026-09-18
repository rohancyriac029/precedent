// ============================================================
// RESULTS RENDERER — converts API response → DOM
// ============================================================

const VERDICT_LABELS = {
  GROUNDED: 'Grounded',
  RARE: 'Rare',
  UNPRECEDENTED_IN_CORPUS: 'No Precedent',
  UNSUPPORTED: 'Unsupported',
  UNKNOWN_SERVICE: 'Unknown',
};

const SERVICE_DISPLAY = {
  s3: 'S3',
  lambda: 'Lambda',
  dynamodb: 'DynamoDB',
  sqs: 'SQS',
  sns: 'SNS',
  eventbridge: 'EventBridge',
  step_functions: 'Step Functions',
  apigateway: 'API Gateway',
  kinesis_streams: 'Kinesis',
  firehose: 'Firehose',
  cognito: 'Cognito',
  secrets_manager: 'Secrets Mgr',
  iot: 'IoT Core',
  rekognition: 'Rekognition',
  textract: 'Textract',
  comprehend: 'Comprehend',
  bedrock: 'Bedrock',
  alb: 'ALB',
  rds: 'RDS',
};

function svcLabel(svc) {
  return SERVICE_DISPLAY[svc] || svc.replace(/_/g, ' ');
}

function escapeHTML(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function renderResults(report, container) {
  const { edges = [], nodes = [], closest_patterns = [], summary = '', fingerprint = '', timings_ms = {}, limitations = [] } = report;

  // Summary bar
  const summaryBar = `
    <div class="results-summary-bar">
      <p class="results-summary-text">${escapeHTML(summary)}</p>
      <div class="results-meta">
        <span class="results-timing">${timings_ms.total ? `${timings_ms.total}ms` : ''}</span>
        <span class="results-fingerprint" title="Deterministic fingerprint — identical runs produce identical hashes">${fingerprint ? fingerprint.slice(0, 32) + '…' : ''}</span>
      </div>
    </div>`;

  // Legend
  const legend = `
    <div class="verdict-legend">
      <div class="legend-item"><span class="legend-pip" style="background:#0f5c3a"></span>Grounded</div>
      <div class="legend-item"><span class="legend-pip" style="background:#7a5200"></span>Rare</div>
      <div class="legend-item"><span class="legend-pip" style="background:#1e3a5f"></span>No Precedent</div>
      <div class="legend-item"><span class="legend-pip" style="background:#7a1a1a"></span>Unsupported</div>
    </div>`;

  // Edge cards
  const edgesHTML = edges.map((ev) => {
    const badgeClass = ev.label;
    const badgeText = VERDICT_LABELS[ev.label] || ev.label;
    const countText = ev.count > 0
      ? `${ev.count} pattern${ev.count !== 1 ? 's' : ''} in corpus`
      : 'not in corpus';

    // Evidence chips
    const evidenceHTML = ev.evidence && ev.evidence.length > 0
      ? `<div class="evidence-list">
          ${ev.evidence.slice(0, 5).map(e =>
            `<a class="evidence-chip" href="${escapeHTML(e.url)}" target="_blank" rel="noopener" title="${escapeHTML(e.title)}">${escapeHTML(e.pattern_id)}</a>`
          ).join('')}
        </div>`
      : '';

    // Repair paths
    const repairsHTML = ev.repairs && ev.repairs.length > 0
      ? `<div class="repair-paths">
          <div class="repair-label">Suggested Routes</div>
          ${ev.repairs.map((rep) => `
            <div class="repair-path">
              ${rep.services.map((svc, i) => `
                <span class="repair-hop ${i > 0 && i < rep.services.length - 1 ? 'intermediate' : ''}">${escapeHTML(svcLabel(svc))}</span>
                ${i < rep.services.length - 1 ? '<span class="edge-arrow">→</span>' : ''}
              `).join('')}
              ${rep.hop_counts && rep.hop_counts.length > 0
                ? `<span class="repair-count">(${rep.hop_counts.join(', ')} patterns)</span>`
                : ''}
            </div>
          `).join('')}
        </div>`
      : '';

    return `
      <div class="edge-card ${badgeClass}">
        <div>
          <div class="edge-route">
            <span class="edge-service">${escapeHTML(svcLabel(ev.src_service))}</span>
            <span class="edge-arrow">→</span>
            <span class="edge-service">${escapeHTML(svcLabel(ev.dst_service))}</span>
          </div>
          <div class="edge-count">${countText}</div>
          ${evidenceHTML}
        </div>
        <div><span class="verdict-badge ${badgeClass}">${badgeText}</span></div>
        ${repairsHTML}
      </div>`;
  }).join('');

  // Node warnings
  const nodeWarnings = nodes.filter(n => n.flags && n.flags.length > 0);
  const warningsHTML = nodeWarnings.length > 0
    ? `<div class="node-warnings">
        <div class="section-heading">Architecture Observations</div>
        ${nodeWarnings.map(nv => `
          <div class="warning-card">
            <span class="warning-icon">${nv.flags.includes('OVERLAPPING_CAPABILITY') ? '◇' : '○'}</span>
            <span class="warning-text">${nv.note ? escapeHTML(nv.note) : nv.flags.join(', ')}</span>
          </div>
        `).join('')}
      </div>`
    : '';

  // Closest patterns
  const patternsHTML = closest_patterns.length > 0
    ? `<div class="patterns-section">
        <div class="section-heading">Closest Verified Patterns</div>
        <div class="patterns-grid">
          ${closest_patterns.slice(0, 6).map(p => `
            <a class="pattern-card" href="${escapeHTML(p.url)}" target="_blank" rel="noopener">
              <div class="pattern-containment">${Math.round(p.containment * 100)}%</div>
              <div class="pattern-name">${escapeHTML(p.title)}</div>
              <div class="pattern-edges-preview">
                ${(p.shared_edges || []).map(([a, b]) =>
                  `<span class="pattern-edge-tag">${escapeHTML(svcLabel(a))} → ${escapeHTML(svcLabel(b))}</span>`
                ).join('')}
              </div>
              <span class="pattern-link-arrow">↗</span>
            </a>
          `).join('')}
        </div>
      </div>`
    : '';

  // Limitations
  const limitationsHTML = limitations.length > 0
    ? `<div class="limitations-section">
        <div class="section-heading">Scope & Limitations</div>
        <ul class="limitations-list">
          ${limitations.map(l => `<li>${escapeHTML(l)}</li>`).join('')}
        </ul>
      </div>`
    : '';

  container.innerHTML = `
    ${summaryBar}
    ${legend}
    <div class="section-heading">Connection Verdicts</div>
    <div class="edges-grid">${edgesHTML}</div>
    ${warningsHTML}
    ${patternsHTML}
    ${limitationsHTML}
  `;

  container.classList.add('visible');

  // Stagger-animate edge cards
  const cards = container.querySelectorAll('.edge-card');
  cards.forEach((card, i) => {
    card.style.opacity = '0';
    card.style.transform = 'translateY(16px)';
    setTimeout(() => {
      card.style.transition = 'opacity 0.4s ease, transform 0.4s cubic-bezier(0.16,1,0.3,1)';
      card.style.opacity = '1';
      card.style.transform = 'translateY(0)';
    }, 80 + i * 60);
  });
}

export function renderConfirmDialog(draft, onConfirm, onDiscard) {
  const { nodes = [], edges = [] } = draft;

  const nodesHTML = nodes.map(n =>
    `<div class="confirm-node">
      <span class="confirm-node-id">${escapeHTML(n.id)}</span>
      <span class="confirm-node-service">${escapeHTML(svcLabel(n.service))}</span>
      <span style="color:var(--ink-40); font-size:0.65rem; margin-left:auto; font-family:var(--font-mono)">${escapeHTML(n.label)}</span>
    </div>`
  ).join('');

  const edgesHTML = edges.map(e => {
    const srcNode = nodes.find(n => n.id === e.src);
    const dstNode = nodes.find(n => n.id === e.dst);
    return `<div class="confirm-edge">
      <span>${escapeHTML(svcLabel(srcNode?.service || e.src))}</span>
      <span style="color:var(--ink-40)">→</span>
      <span>${escapeHTML(svcLabel(dstNode?.service || e.dst))}</span>
      <span style="color:var(--ink-40); margin-left:auto; font-size:0.65rem">${escapeHTML(e.relation)}</span>
    </div>`;
  }).join('');

  const repairs = draft.repairs_applied || {};
  const repairNote = (repairs.removed?.length || repairs.added?.length)
    ? `<p style="font-size:0.72rem; color:rgba(244,241,235,0.35); margin-bottom:1rem; line-height:1.5">
        Model output was cleaned up automatically: 
        ${repairs.removed?.length ? `${repairs.removed.length} edge(s) removed` : ''}
        ${repairs.added?.length ? `${repairs.added.length} edge(s) added` : ''}.
      </p>`
    : '';

  const dialog = document.createElement('div');
  dialog.className = 'confirm-overlay';
  dialog.innerHTML = `
    <div class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
      <h2 class="confirm-title" id="confirm-title">Review Extracted Graph</h2>
      <p class="confirm-sub">Bedrock extracted this draft from your description.
        Verify it looks correct before the deterministic audit runs.<br>
        No model is involved in the audit itself.</p>
      ${repairNote}
      <div class="confirm-graph-preview">
        <div style="font-family:var(--font-mono); font-size:0.6rem; letter-spacing:0.1em; text-transform:uppercase; color:rgba(244,241,235,0.25); margin-bottom:0.5rem">Nodes</div>
        ${nodesHTML}
        <div class="confirm-edge-list">
          <div style="font-family:var(--font-mono); font-size:0.6rem; letter-spacing:0.1em; text-transform:uppercase; color:rgba(244,241,235,0.25); margin-bottom:0.5rem">Edges</div>
          ${edgesHTML}
        </div>
      </div>
      <div class="confirm-actions">
        <button class="btn-confirm" id="confirm-yes">Looks correct — Audit this</button>
        <button class="btn-discard" id="confirm-no">Discard & rewrite</button>
      </div>
    </div>`;

  document.body.appendChild(dialog);
  requestAnimationFrame(() => dialog.classList.add('visible'));

  dialog.querySelector('#confirm-yes').addEventListener('click', () => {
    dialog.remove();
    onConfirm(draft);
  });

  dialog.querySelector('#confirm-no').addEventListener('click', () => {
    dialog.remove();
    onDiscard();
  });

  // Close on backdrop click
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) {
      dialog.remove();
      onDiscard();
    }
  });
}
