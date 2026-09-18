import './style.css';
import { runAudit, extractProse, getHealth, getCorpusStats } from './api.js';
import { renderResults, renderConfirmDialog } from './renderer.js';
import { SAMPLES } from './samples.js';

// ============================================================
// DOM MOUNT
// ============================================================

document.querySelector('#app').innerHTML = `
<!-- NAV -->
<nav class="nav" id="main-nav" role="navigation" aria-label="Main navigation">
  <a class="nav-logo" href="#" aria-label="Precedent home">
    <span class="nav-logo-dot" aria-hidden="true"></span>
    Precedent
  </a>
  <div class="nav-actions">
    <button class="nav-link" id="nav-audit" aria-label="Jump to audit workspace">Audit</button>
    <button class="nav-link" id="nav-about" aria-label="Jump to about section">About</button>
    <a class="nav-link" href="https://ob72bcbovd.execute-api.ap-south-1.amazonaws.com/v1/health" target="_blank" rel="noopener" aria-label="View live API">API ↗</a>
  </div>
</nav>

<!-- HERO -->
<section class="hero" id="hero" aria-labelledby="hero-heading">
  <div class="hero-content">
    <p class="hero-eyebrow" aria-hidden="true">AWS Architecture Auditor</p>
    <h1 class="hero-headline" id="hero-heading">
      Ground your architecture<br>
      in <em>reality.</em>
    </h1>
    <p class="hero-sub">
      Precedent checks every connection in your AWS design against 1,000+ 
      verified, deployable patterns. Not hallucination. Not convention. Proof.
    </p>
    <div class="hero-cta-row">
      <button class="btn-primary" id="hero-cta">
        <span>↓</span>
        <span>Audit an Architecture</span>
      </button>
      <a class="btn-ghost-dark" href="https://github.com/aws-samples/serverless-patterns" target="_blank" rel="noopener">
        <span>1,040 Patterns ↗</span>
      </a>
    </div>
  </div>
  <div class="hero-ticker" aria-label="Corpus statistics">
    <div class="ticker-item">
      <span class="ticker-num" id="stat-patterns">1,040</span>
      <span class="ticker-label">Patterns in corpus</span>
    </div>
    <div class="ticker-divider" aria-hidden="true"></div>
    <div class="ticker-item">
      <span class="ticker-num" id="stat-edges">65</span>
      <span class="ticker-label">Service pairs indexed</span>
    </div>
    <div class="ticker-divider" aria-hidden="true"></div>
    <div class="ticker-item">
      <span class="ticker-num" id="stat-tests">205</span>
      <span class="ticker-label">Tests passing</span>
    </div>
    <div class="ticker-divider" aria-hidden="true"></div>
    <div class="ticker-item">
      <span class="ticker-num">50ms</span>
      <span class="ticker-label">Audit latency</span>
    </div>
  </div>
</section>

<!-- WORKSPACE -->
<section class="workspace" id="workspace" aria-labelledby="workspace-heading">
  <div class="workspace-inner">
    <div class="workspace-header">
      <div>
        <h2 class="workspace-title" id="workspace-heading">
          Submit your<br><em>architecture.</em>
        </h2>
      </div>
      <div>
        <div class="status-indicator" aria-live="polite">
          <span class="status-dot" id="api-dot" aria-hidden="true"></span>
          <span id="api-status">checking api…</span>
        </div>
      </div>
    </div>

    <!-- Input mode tabs -->
    <div class="input-tabs" role="tablist" aria-label="Input type">
      <button class="input-tab active" role="tab" aria-selected="true" aria-controls="panel-mermaid" id="tab-mermaid" data-mode="mermaid">Mermaid</button>
      <button class="input-tab" role="tab" aria-selected="false" aria-controls="panel-template" id="tab-template" data-mode="template">SAM / CFN</button>
      <button class="input-tab" role="tab" aria-selected="false" aria-controls="panel-prose" id="tab-prose" data-mode="prose">Prose</button>
    </div>

    <!-- Mermaid Panel -->
    <div id="panel-mermaid" role="tabpanel" aria-labelledby="tab-mermaid">
      <div class="editor-area">
        <label class="editor-label" for="mermaid-input">Mermaid diagram</label>
        <div class="sample-inputs" aria-label="Load a sample diagram">
          ${SAMPLES.mermaid.map((s, i) => `<button class="sample-btn" data-mode="mermaid" data-index="${i}">${s.label}</button>`).join('')}
        </div>
        <textarea
          class="editor-input"
          id="mermaid-input"
          placeholder="flowchart LR&#10;  S3[Bucket] --> Lambda[Function]&#10;  Lambda --> DDB[(Table)]"
          spellcheck="false"
          aria-label="Enter Mermaid diagram"
        ></textarea>
      </div>
    </div>

    <!-- Template Panel -->
    <div id="panel-template" role="tabpanel" aria-labelledby="tab-template" hidden>
      <div class="editor-area">
        <label class="editor-label" for="template-input">SAM / CloudFormation YAML</label>
        <div class="sample-inputs" aria-label="Load a sample template">
          ${SAMPLES.template.map((s, i) => `<button class="sample-btn" data-mode="template" data-index="${i}">${s.label}</button>`).join('')}
        </div>
        <textarea
          class="editor-input"
          id="template-input"
          placeholder="AWSTemplateFormatVersion: '2010-09-09'&#10;Transform: AWS::Serverless-2016-10-31&#10;&#10;Resources:&#10;  ..."
          spellcheck="false"
          aria-label="Enter SAM or CloudFormation template YAML"
        ></textarea>
      </div>
    </div>

    <!-- Prose Panel -->
    <div id="panel-prose" role="tabpanel" aria-labelledby="tab-prose" hidden>
      <div class="editor-area">
        <label class="editor-label" for="prose-input">Describe your architecture in plain English</label>
        <div class="sample-inputs" aria-label="Load a sample description">
          ${SAMPLES.prose.map((s, i) => `<button class="sample-btn" data-mode="prose" data-index="${i}">${s.label}</button>`).join('')}
        </div>
        <textarea
          class="editor-input prose-input"
          id="prose-input"
          placeholder="Users upload images to S3 which triggers a Lambda function…"
          spellcheck="true"
          aria-label="Describe your architecture in plain English"
        ></textarea>
        <p style="margin-top:0.6rem; font-family:var(--font-sans); font-size:0.8rem; color:rgba(244,241,235,0.65); line-height:1.5;">
          Bedrock extracts a graph from your description, then you confirm it before the deterministic audit runs. The audit itself uses no model.
        </p>
      </div>
    </div>

    <!-- Submit -->
    <div class="submit-row">
      <button class="btn-audit" id="audit-btn" aria-label="Run audit">
        <span>Run Audit</span>
      </button>
      <button class="btn-clear" id="clear-btn" aria-label="Clear results">Clear</button>
      <button class="btn-export" id="export-btn" aria-label="Save audit report as PDF">
        <span class="btn-export-icon">↓</span>
        <span>Save PDF</span>
      </button>
    </div>

    <!-- Loading -->
    <div class="loading-state" id="loading-state" aria-live="polite" aria-label="Loading">
      <div class="loading-bar-track"><div class="loading-bar-fill"></div></div>
      <span class="loading-text" id="loading-text">Running audit…</span>
    </div>

    <!-- Error -->
    <div class="error-state" id="error-state" role="alert">
      <div class="error-title">Audit Failed</div>
      <div class="error-message" id="error-message"></div>
    </div>

    <!-- Results -->
    <div class="results" id="results" aria-live="polite" aria-label="Audit results"></div>
  </div>
</section>

<!-- ABOUT -->
<section class="about" id="about" aria-labelledby="about-heading">
  <div class="about-grid">
    <h2 class="about-main-heading" id="about-heading">
      Architecture<br>
      grounded in<br>
      evidence.
    </h2>
    <p class="about-description">
      Every verdict is computed deterministically from a corpus of 1,040 
      deployable AWS architectures. No model decides what is correct. 
      Corpus counts, integration rules, and weighted graph repairs do.
    </p>
    <div class="about-principles">
      <div class="principle">
        <div class="principle-number">01 — Deterministic</div>
        <h3 class="principle-title">Same input, same result.</h3>
        <p class="principle-body">
          Every audit is fingerprinted with a SHA-256 hash. Run the same 
          diagram against the same corpus commit and the result is identical 
          down to the byte. No probabilistic inference ever produces a verdict.
        </p>
      </div>
      <div class="principle">
        <div class="principle-number">02 — Grounded</div>
        <h3 class="principle-title">Precedent, not convention.</h3>
        <p class="principle-body">
          GROUNDED means ≥ 3 distinct, deployable AWS patterns contain that 
          service connection. UNPRECEDENTED means zero. Absence from the corpus 
          is never treated as evidence of impossibility — we only know what we 
          can prove.
        </p>
      </div>
      <div class="principle">
        <div class="principle-number">03 — Honest</div>
        <h3 class="principle-title">Uncertainty surfaced, not hidden.</h3>
        <p class="principle-body">
          Integration rules are only applied once a human has verified them 
          against official AWS documentation and added their initials. 
          Unverified rules are listed, counted in the limitations, and do 
          not affect any verdict.
        </p>
      </div>
    </div>
  </div>
</section>

<!-- FOOTER -->
<footer class="footer" aria-label="Footer">
  <div class="footer-inner">
    <div>
      <div class="footer-logo">Precedent</div>
      <div class="footer-sub">
        Corpus: aws-samples/serverless-patterns · Region: ap-south-1
      </div>
    </div>
    <nav class="footer-links" aria-label="Footer links">
      <a class="footer-link" href="https://ob72bcbovd.execute-api.ap-south-1.amazonaws.com/v1/health" target="_blank" rel="noopener">Health</a>
      <a class="footer-link" href="https://ob72bcbovd.execute-api.ap-south-1.amazonaws.com/v1/rules" target="_blank" rel="noopener">Rules</a>
      <a class="footer-link" href="https://ob72bcbovd.execute-api.ap-south-1.amazonaws.com/v1/corpus/stats" target="_blank" rel="noopener">Stats</a>
    </nav>
  </div>
</footer>
`;

// ============================================================
// STATE
// ============================================================

let currentMode = 'mermaid';

const el = (id) => document.getElementById(id);

const nav = el('main-nav');
const auditBtn = el('audit-btn');
const clearBtn = el('clear-btn');
const exportBtn = el('export-btn');
const loadingState = el('loading-state');
const loadingText = el('loading-text');
const errorState = el('error-state');
const errorMessage = el('error-message');
const resultsContainer = el('results');
const apiDot = el('api-dot');
const apiStatus = el('api-status');

// Print header element (hidden normally, shown in @media print)
const printHeader = document.createElement('div');
printHeader.className = 'print-header';
printHeader.innerHTML = `
  <h1>Precedent Audit Report</h1>
  <p id="print-meta"></p>
`;
resultsContainer.prepend(printHeader);

function updatePrintMeta(report) {
  const meta = document.getElementById('print-meta');
  if (!meta) return;
  const now = new Date().toLocaleString();
  const fp = report.fingerprint ? report.fingerprint.slice(0, 16) + '…' : '';
  meta.textContent = `Generated ${now} · Corpus commit ${report.corpus?.commit?.slice(0,8) || 'unknown'} · SHA-256: ${fp}`;
}

// ============================================================
// NAV SCROLL
// ============================================================

window.addEventListener('scroll', () => {
  nav.classList.toggle('scrolled', window.scrollY > 40);
}, { passive: true });

// Nav buttons
el('nav-audit').addEventListener('click', () => {
  el('workspace').scrollIntoView({ behavior: 'smooth' });
});

el('nav-about').addEventListener('click', () => {
  el('about').scrollIntoView({ behavior: 'smooth' });
});

el('hero-cta').addEventListener('click', () => {
  el('workspace').scrollIntoView({ behavior: 'smooth' });
});

// ============================================================
// INPUT TABS
// ============================================================

const tabs = document.querySelectorAll('.input-tab');
const panels = {
  mermaid: el('panel-mermaid'),
  template: el('panel-template'),
  prose: el('panel-prose'),
};

tabs.forEach(tab => {
  tab.addEventListener('click', () => {
    const mode = tab.dataset.mode;
    currentMode = mode;

    tabs.forEach(t => {
      t.classList.toggle('active', t === tab);
      t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
    });

    Object.entries(panels).forEach(([key, panel]) => {
      panel.hidden = key !== mode;
    });

    clearResults();
  });
});

// ============================================================
// SAMPLE INPUTS
// ============================================================

document.querySelectorAll('.sample-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const mode = btn.dataset.mode;
    const index = parseInt(btn.dataset.index, 10);
    const sample = SAMPLES[mode][index];
    if (!sample) return;

    // Switch to correct tab first
    const targetTab = document.querySelector(`[data-mode="${mode}"].input-tab`);
    if (targetTab) targetTab.click();

    const inputEl = el(`${mode}-input`);
    if (inputEl) {
      inputEl.value = sample.content;
      inputEl.focus();
    }
  });
});

// ============================================================
// AUDIT SUBMISSION
// ============================================================

function getInputContent() {
  return el(`${currentMode}-input`)?.value?.trim() || '';
}

function setLoading(show, text = 'Running audit…') {
  loadingState.classList.toggle('visible', show);
  loadingText.textContent = text;
  auditBtn.disabled = show;
}

function showError(msg) {
  errorState.classList.add('visible');
  errorMessage.textContent = msg;
}

function clearResults() {
  resultsContainer.innerHTML = '';
  resultsContainer.prepend(printHeader);
  resultsContainer.classList.remove('visible');
  errorState.classList.remove('visible');
  errorMessage.textContent = '';
  exportBtn.classList.remove('visible');
}

async function runAuditFlow() {
  const content = getInputContent();
  if (!content) {
    showError('Please enter an architecture to audit.');
    return;
  }

  clearResults();
  setLoading(true, currentMode === 'prose' ? 'Extracting via Bedrock…' : 'Running audit…');

  try {
    let report;

    if (currentMode === 'prose') {
      // Two-step: extract → confirm → audit
      const draft = await extractProse(content);

      setLoading(false);

      if (draft.confirm_required) {
        await new Promise((resolve, reject) => {
          renderConfirmDialog(
            draft,
            async (confirmedDraft) => {
              setLoading(true, 'Running deterministic audit…');
              try {
                report = await runAudit({ graph: confirmedDraft });
                updatePrintMeta(report);
                renderResults(report, resultsContainer);
                exportBtn.classList.add('visible');
                resultsContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
              } catch (err) {
                showError(err.message || 'Audit failed after confirmation.');
              } finally {
                setLoading(false);
              }
              resolve();
            },
            () => resolve()
          );
        });
      } else {
        report = await runAudit({ graph: draft });
        renderResults(report, resultsContainer);
        resultsContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }

    } else {
      // Direct audit
      report = await runAudit({ input_type: currentMode, content });
      updatePrintMeta(report);
      renderResults(report, resultsContainer);
      exportBtn.classList.add('visible');
      resultsContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

  } catch (err) {
    showError(err.message || 'An unexpected error occurred. Is the API server running?');
  } finally {
    setLoading(false);
  }
}

auditBtn.addEventListener('click', runAuditFlow);

clearBtn.addEventListener('click', () => {
  clearResults();
  const inputEl = el(`${currentMode}-input`);
  if (inputEl) inputEl.value = '';
});

exportBtn.addEventListener('click', () => {
  window.print();
});

// Ctrl+Enter / Cmd+Enter to submit
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    runAuditFlow();
  }
});

// ============================================================
// API HEALTH CHECK
// ============================================================

async function checkAPIHealth() {
  try {
    const h = await getHealth();
    apiDot.className = 'status-dot active';
    apiStatus.textContent = `api live · ${h.rules_total} rules · ${h.patterns_indexed} patterns`;
  } catch {
    apiDot.className = 'status-dot error';
    apiStatus.textContent = 'api unavailable';
  }
}

// ============================================================
// CORPUS STATS (update hero ticker)
// ============================================================

async function loadCorpusStats() {
  try {
    const stats = await getCorpusStats();
    if (stats.patterns_indexed > 0) {
      el('stat-patterns').textContent = stats.patterns_indexed.toLocaleString();
    }
    if (stats.edges_indexed > 0) {
      el('stat-edges').textContent = stats.edges_indexed.toLocaleString();
    }
  } catch {
    // silently degrade
  }
}

// ============================================================
// HERO NUMBER ANIMATION
// ============================================================

function animateNumber(el, target, duration = 1200) {
  const start = 0;
  const startTime = performance.now();
  const suffix = el.textContent.replace(/[0-9,]/g, '');

  const update = (now) => {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const value = Math.floor(eased * target);
    el.textContent = value.toLocaleString() + suffix;
    if (progress < 1) requestAnimationFrame(update);
  };

  requestAnimationFrame(update);
}

// Observe hero ticker to trigger animation once in viewport
const tickerObs = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      animateNumber(el('stat-tests'), 205, 1000);
      tickerObs.disconnect();
    }
  });
}, { threshold: 0.5 });

const ticker = document.querySelector('.hero-ticker');
if (ticker) tickerObs.observe(ticker);

// ============================================================
// BOOT
// ============================================================

checkAPIHealth();
loadCorpusStats();
