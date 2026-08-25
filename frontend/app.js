/* ═══════════════════════════════════════════════════════
   reclaim. — Dashboard JavaScript
   ═══════════════════════════════════════════════════════ */

const API = 'http://localhost:8000';
let allTransactions = [];
let sortKey = null, sortAsc = true;
let statsRefreshTimer = null;
let feedEvents = [];  // Cached for feed tab
let synth = window.speechSynthesis;
let voiceUtterance = null;

// ── Init ───────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadMerchantProfile();
  checkHealth();
  loadStats();
  loadTransactionTable();
  loadAuditTrail();
  statsRefreshTimer = setInterval(loadStats, 6000);
});

async function loadMerchantProfile() {
  try {
    const data = await apiFetch('/api/auth/me');
    document.getElementById('merchant-name-display').textContent = data.name;
  } catch (err) {
    console.error("Failed to load merchant profile", err);
    logout();
  }
}

function logout() {
  sessionStorage.removeItem("reclaim_token");
  window.location.href = "/";
}

// ── Tab Navigation ─────────────────────────────────────
const TAB_TITLES = {
  overview:     { title: 'Overview',          badge: 'AI Revenue Recovery' },
  feed:         { title: 'AI Decision Feed',   badge: 'Real-time reasoning' },
  transactions: { title: 'Transactions',       badge: 'Payment ledger' },
  compliance:   { title: 'Compliance Panel',   badge: 'Stop rules & escalations' },
  audit:        { title: 'Audit Trail',        badge: 'Full event log' },
};

function switchTab(tab, el) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  el.classList.add('active');
  const meta = TAB_TITLES[tab] || {};
  document.getElementById('page-title').textContent = meta.title || tab;
  document.getElementById('page-badge').textContent = meta.badge || '';

  if (tab === 'feed')        renderFeed();
  if (tab === 'compliance')  loadCompliance();
  if (tab === 'audit')       loadAuditTrail();
}

// ── Health ─────────────────────────────────────────────
async function checkHealth() {
  try {
    const d = await apiFetch('/api/health');
    const dot  = document.querySelector('.status-dot');
    const text = document.getElementById('status-text');
    if (d.razorpay_configured) {
      dot.className = 'status-dot ok';
      text.textContent = 'Razorpay Live';
    } else {
      dot.className = 'status-dot warning';
      text.textContent = 'Simulation Mode';
    }
  } catch {
    document.getElementById('status-text').textContent = 'API Offline';
  }
}

// ── Stats ──────────────────────────────────────────────
async function loadStats() {
  try {
    const s = await apiFetch('/api/stats');
    setMetric('m-recovered', '₹' + fmtINR(s.amount_recovered / 100));
    setMetric('m-rate', s.recovery_rate + '%');
    setMetric('m-risk', '₹' + fmtINR(s.amount_at_risk / 100));
    document.getElementById('m-recovered-sub').textContent = s.recovered + ' recovered entities';
    document.getElementById('m-risk-sub').textContent = s.total_transactions + ' entities at risk';
    document.getElementById('m-time').textContent = fmtTime(s.avg_recovery_time_seconds);

    // Progress bar on rate card
    document.getElementById('m-rate-bar').style.width = s.recovery_rate + '%';

    // Funnel
    const total = s.total_transactions || 1;
    document.getElementById('f-total').textContent = s.total_transactions;
    document.getElementById('f-actionable').textContent = s.actionable || (s.total_transactions - (s.escalated || 0));
    document.getElementById('f-recovered').textContent = s.recovered;
    document.getElementById('f-revenue').textContent = '₹' + fmtINR(s.amount_recovered / 100);
    document.getElementById('f-actionable-bar').style.width = Math.min(((s.actionable || 0) / total) * 100, 100) + '%';
    document.getElementById('f-recovered-bar').style.width = Math.min((s.recovered / total) * 100, 100) + '%';

    // Comparison
    const recovered = s.amount_recovered / 100;
    document.getElementById('comp-value').textContent = '₹' + fmtINR(recovered);
    const pct = recovered > 0 ? Math.min(96, Math.max(30, (s.recovery_rate / 100) * 96)) : 4;
    document.getElementById('comp-bar').style.width = pct + '%';
    if (recovered > 0) {
      document.getElementById('comp-uplift').textContent =
        `↗ ₹${fmtINR(recovered)} recovered that would have been lost. ${s.recovery_rate}% recovery rate.`;
    }

    // Action breakdown
    renderActionBreakdown(s.action_breakdown || {});

    // Source breakdown
    renderSourceBreakdown(s.source_breakdown || {});

    // Failure grid
    renderFailureGrid(s.failure_breakdown || {});

  } catch (e) {
    console.warn('Stats:', e.message);
  }
}

function renderActionBreakdown(breakdown) {
  const el = document.getElementById('ab-rows');
  const items = Object.entries(breakdown).filter(([k]) => k !== 'DETECTED');
  if (!items.length) { el.innerHTML = '<div class="empty-hint">No actions yet</div>'; return; }
  const max = Math.max(...items.map(([,v]) => v));
  const colors = {
    'DIAGNOSIS': '#0B72E7', 'AUTO_RETRY': '#F59E0B',
    'PAYMENT_LINK_SENT': '#16A34A', 'PAYMENT_LINK': '#16A34A', 'ESCALATED': '#DC2626',
    'STOP_RULE_APPLIED': '#64748B', 'EMI_OFFER_SENT': '#8B5CF6', 'EMI_OFFER': '#8B5CF6',
    'SEND_REMINDER': '#6366F1', 'VOICE_CALL': '#7C3AED'
  };
  el.innerHTML = items.sort((a,b) => b[1]-a[1]).map(([k, v]) => `
    <div class="ab-row" style="margin-bottom: 0.35rem;">
      <div class="ab-label" style="width: 100px; flex-shrink: 0; font-size: 0.8rem;">${fmtLabel(k)}</div>
      <div class="ab-bar-wrap" style="flex: 1; height: 4px; background: #E2E8F0; border-radius: 2px;"><div class="ab-bar" style="width:${(v/max)*100}%; height: 100%; border-radius: 2px; background:${colors[k]||'#94A3B8'}"></div></div>
      <div class="ab-count" style="margin-left: 0.5rem; font-weight: 700;">${v}</div>
    </div>`).join('');
}

function renderSourceBreakdown(breakdown) {
  const el = document.getElementById('sb-rows');
  if (!el) return;
  const items = Object.entries(breakdown);
  if (!items.length) { el.innerHTML = '<div class="empty-hint">No source stats yet</div>'; return; }
  
  const colors = {
    'payment': '#0B72E7',
    'checkout': '#7E22CE',
    'subscription': '#16A34A',
    'invoice': '#F59E0B'
  };
  
  el.innerHTML = items.map(([k, v]) => {
    const recoveredVal = v.recovered / 100;
    const atRiskVal = v.at_risk / 100;
    const rate = v.count > 0 ? ((v.recovered_count / v.count) * 100).toFixed(1) : 0.0;
    
    return `
      <div class="ab-row" style="margin-bottom: 0.75rem; flex-direction: column; align-items: flex-start;">
        <div style="display: flex; justify-content: space-between; width: 100%; font-size: 0.75rem; font-weight: 600; margin-bottom: 0.25rem;">
          <span style="color: ${colors[k]}">${k.toUpperCase()}</span>
          <span style="color: #475569">₹${fmtINR(recoveredVal)} / ₹${fmtINR(atRiskVal)} (${rate}%)</span>
        </div>
        <div class="ab-bar-wrap" style="width: 100%; height: 6px; background: #F1F5F9; border-radius: 3px;">
          <div class="ab-bar" style="width: ${rate}%; height: 100%; border-radius: 3px; background: ${colors[k]}"></div>
        </div>
      </div>
    `;
  }).join('');
}

function renderFailureGrid(breakdown) {
  const el = document.getElementById('failure-grid');
  if (!Object.keys(breakdown).length) { el.innerHTML = '<div class="empty-hint">Load transactions to see breakdown</div>'; return; }
  const colors = {
    BANK_DOWNTIME:'#0B72E7', NETWORK_TIMEOUT:'#8B5CF6', UPI_TIMEOUT:'#06B6D4',
    INSUFFICIENT_FUNDS:'#DC2626', CARD_EXPIRED:'#F59E0B', WRONG_CVV:'#F97316',
    LIMIT_EXCEEDED:'#EAB308', MANDATE_FAILED:'#A855F7', FRAUD_FLAGGED:'#DC2626', CARD_BLOCKED:'#6B7280',
    PRICE_DROP_OFF: '#EC4899', FRICTION_DROP_OFF: '#F43F5E', DISTRACTION_DROP_OFF: '#D946EF',
    WILL_PAY_SOON: '#10B981', NEED_REMINDER: '#F59E0B', HIGH_RISK_DEFAULT: '#EF4444'
  };
  el.innerHTML = Object.entries(breakdown).sort((a,b)=>b[1]-a[1]).map(([k,v]) => `
    <div class="failure-chip">
      <span class="failure-chip-dot" style="background:${colors[k]||'#94A3B8'}"></span>
      <span class="failure-chip-label">${fmtLabel(k)}</span>
      <span class="failure-chip-count">${v}</span>
    </div>`).join('');
}

// ── Load / Run / Simulation ───────────────────────────
async function loadTransactions() {
  const btn = document.getElementById('btn-load');
  btn.disabled = true; btn.textContent = 'Loading...';
  try {
    showToast('Loading failed payments...', 'info');
    const d = await apiFetch('/api/load-transactions', { method: 'POST' });
    showToast(`✓ Loaded ${d.total} failed transactions (${d.source})`, 'success');
    document.getElementById('btn-recover').disabled = false;
    await loadTransactionTable();
    await loadStats();
  } catch (e) {
    showToast('Load failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg> Load Payments`;
  }
}

async function runRecovery() {
  const btn = document.getElementById('btn-recover');
  btn.disabled = true;
  btn.className = 'btn btn-primary running';
  btn.textContent = '⏳ Running...';

  openProgressDrawer();
  const logSteps = [
    { icon: '🔍', text: '<strong>Detection complete.</strong> failed payments classified.' },
    { icon: '🧠', text: 'Gemini AI analyzing root causes for payments...' },
    { icon: '⚡', text: 'Transient failures → <strong>AUTO_RETRY</strong> scheduled.' },
    { icon: '🔗', text: 'Soft declines → <strong>PAYMENT_LINK</strong> links sent.' },
    { icon: '🛡️', text: '<strong>Stop rules enforced</strong>: FRAUD_FLAGGED & CARD_BLOCKED escalated.' },
    { icon: '✅', text: 'Batch complete. Calculating final metrics...' },
  ];
  let i = 0;
  const logTimer = setInterval(() => {
    if (i < logSteps.length) {
      addProgressLog(logSteps[i].icon, logSteps[i].text);
      const pct = Math.round(((i + 1) / logSteps.length) * 100);
      document.getElementById('pd-bar').style.width = pct + '%';
      document.getElementById('pd-pct').textContent = pct + '%';
      i++;
    } else { clearInterval(logTimer); }
  }, 650);

  try {
    const result = await apiFetch('/api/run-recovery', { method: 'POST' });
    clearInterval(logTimer);
    document.getElementById('pd-bar').style.width = '100%';
    document.getElementById('pd-pct').textContent = '100%';
    addProgressLog('🎉', `<strong>Done!</strong> Recovered ₹${fmtINR(result.amount_recovered_inr)} | Rate: <strong>${result.recovery_rate}%</strong>`);
    setTimeout(closeProgressDrawer, 4000);

    showToast(`🎉 ₹${fmtINR(result.amount_recovered_inr)} recovered at ${result.recovery_rate}%`, 'success');

    feedEvents = result.results || [];

    await loadTransactionTable();
    await loadStats();
    await loadAuditTrail();
    if (document.getElementById('tab-feed').classList.contains('active')) renderFeed();
  } catch (e) {
    clearInterval(logTimer);
    addProgressLog('❌', 'Error: ' + e.message);
    showToast('Recovery error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.className = 'btn btn-primary';
    btn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run Agent`;
  }
}

async function runSimulation() {
  const btn = document.getElementById('btn-simulate');
  btn.disabled = true;
  btn.textContent = '⏳ Simulating...';
  
  openProgressDrawer();
  addProgressLog('🏁', '<strong>Starting Unified Platform Simulation...</strong>');
  
  const simulationLogs = [
    { icon: '🔍', text: 'Scanning <strong>checkout_sessions</strong> for inactivity > 10m...' },
    { icon: '🛒', text: 'Detected <strong>20 abandoned checkouts</strong>. Failure type: PRICE_DROP_OFF / FRICTION_DROP_OFF.' },
    { icon: '🔍', text: 'Scanning <strong>subscriptions</strong> table for mandate declines...' },
    { icon: '🔄', text: 'Detected <strong>10 subscription failures</strong>. Sequencing retries (Day 1 -> Day 3 -> Day 5).' },
    { icon: '🔍', text: 'Scanning <strong>invoices</strong> table for overdue credits...' },
    { icon: '🏢', text: 'Detected <strong>10 overdue B2B invoices</strong>. Setting up 48-hour follow-up cooldowns.' },
    { icon: '💳', text: 'Loading <strong>50 transaction payment failures</strong>.' },
    { icon: '🧠', text: 'Running Gemini AI Decision Engine on all <strong>90 recovery targets</strong>...' },
    { icon: '⚖️', text: 'Checking outreach compliance & Do-Not-Contact lists...' },
    { icon: '🛡️', text: 'Block rules applied to 7 FRAUD/BLOCKED accounts. Escalating to risk team.' },
    { icon: '📞', text: 'VoIP Voice Agent generated Hinglish calling scripts for B2B collections.' },
    { icon: '💸', text: 'Processing recovery batch...' },
    { icon: '✅', text: 'Aggregation engine compiling metrics...' }
  ];

  let step = 0;
  const timer = setInterval(() => {
    if (step < simulationLogs.length) {
      addProgressLog(simulationLogs[step].icon, simulationLogs[step].text);
      const pct = Math.round(((step + 1) / (simulationLogs.length + 1)) * 100);
      document.getElementById('pd-bar').style.width = pct + '%';
      document.getElementById('pd-pct').textContent = pct + '%';
      step++;
    } else {
      clearInterval(timer);
    }
  }, 450);

  try {
    const result = await apiFetch('/api/demo/run', { method: 'POST' });
    clearInterval(timer);
    document.getElementById('pd-bar').style.width = '100%';
    document.getElementById('pd-pct').textContent = '100%';
    addProgressLog('🎉', `<strong>Demo Complete!</strong> Recovered ₹${fmtINR(result.amount_recovered_inr)} from ${result.recovered}/${result.total_transactions} targets. Rate: <strong>${result.recovery_rate}%</strong>`);
    setTimeout(closeProgressDrawer, 4000);
    
    showToast(`🎉 Simulation Complete! ₹${fmtINR(result.amount_recovered_inr)} recovered!`, 'success');
    
    feedEvents = result.results || [];
    
    await loadTransactionTable();
    await loadStats();
    await loadAuditTrail();
    if (document.getElementById('tab-feed').classList.contains('active')) renderFeed();
  } catch (e) {
    clearInterval(timer);
    addProgressLog('❌', 'Simulation Error: ' + e.message);
    showToast('Simulation error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run Demo Simulation`;
  }
}

function resetData() {
  if (!confirm('Reset all data and start fresh?')) return;
  window.location.reload();
}

// ── Progress Drawer ────────────────────────────────────
function openProgressDrawer() {
  document.getElementById('progress-drawer').classList.add('open');
  document.getElementById('pd-log').innerHTML = '';
  document.getElementById('pd-bar').style.width = '0%';
  document.getElementById('pd-pct').textContent = '0%';
}
function closeProgressDrawer() {
  document.getElementById('progress-drawer').classList.remove('open');
}
function addProgressLog(icon, text) {
  const log = document.getElementById('pd-log');
  const el = document.createElement('div');
  el.className = 'pd-entry';
  el.innerHTML = `<span class="pd-icon">${icon}</span><span class="pd-text">${text}</span>`;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

// ── Transaction Table ──────────────────────────────────
async function loadTransactionTable() {
  try {
    const d = await apiFetch('/api/transactions?limit=250');
    allTransactions = d.transactions || [];
    renderTable(allTransactions);
  } catch (e) { console.warn('Table:', e.message); }
}

function applyFilters() {
  const status = document.getElementById('filter-status').value;
  const type   = document.getElementById('filter-type').value;
  const source = document.getElementById('filter-source').value;
  let filtered = allTransactions;
  if (status) filtered = filtered.filter(t => t.status === status);
  if (type)   filtered = filtered.filter(t => t.failure_type === type);
  if (source) filtered = filtered.filter(t => t.source_type === source);
  renderTable(filtered, false);
}

function sortTable(key) {
  if (sortKey === key) { sortAsc = !sortAsc; } else { sortKey = key; sortAsc = true; }
  const sorted = [...allTransactions].sort((a, b) => {
    const va = a[key] || 0, vb = b[key] || 0;
    if (typeof va === 'string') return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    return sortAsc ? va - vb : vb - va;
  });
  renderTable(sorted, false);
}

function renderTable(transactions, doSort = true) {
  const tbody = document.getElementById('txn-tbody');
  if (!transactions.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="table-empty">
      <div class="empty-state"><div class="empty-icon">💳</div><div class="empty-title">${allTransactions.length === 0 ? 'Click "Run Demo Simulation" to begin' : 'No transactions match this filter'}</div></div>
    </td></tr>`;
    return;
  }
  const actionLabels = {
    FAILED: '—', RECOVERED: 'Auto-Retry / Link', ESCALATED: 'Escalate', EXHAUSTED: 'Max Retries',
  };
  
  tbody.innerHTML = transactions.map(t => {
    const risk = (t.risk_score || 0) * 100;
    const riskColor = risk > 70 ? '#DC2626' : risk > 40 ? '#F59E0B' : '#16A34A';
    const actionLabel = t.status === 'FAILED' ? 'Pending' : (actionLabels[t.status] || '—');
    
    return `
    <tr onclick="openModal('${t.id}')">
      <td>
        <span class="cell-customer">${t.customer_name || '—'}</span>
        <span class="cell-email">${t.customer_email || ''}</span>
      </td>
      <td>
        <span class="action-tag" style="background: ${getSourceBg(t.source_type)}; color: ${getSourceColor(t.source_type)}; border: none; font-weight:700;">
          ${t.source_type ? t.source_type.toUpperCase() : 'PAYMENT'}
        </span>
      </td>
      <td><span class="action-tag">${fmtLabel(t.failure_type || '—')}</span></td>
      <td>
        <div class="risk-bar-cell">
          <div class="risk-bar-wrap"><div class="risk-bar" style="width:${risk}%;background:${riskColor}"></div></div>
          <span class="risk-label">${(t.risk_score || 0).toFixed(2)}</span>
        </div>
      </td>
      <td><span class="action-tag">${actionLabel}</span></td>
      <td><span class="badge badge-${t.status}">${t.status}</span></td>
      <td class="cell-amount">₹${fmtINR((t.amount || 0) / 100)}</td>
    </tr>`;
  }).join('');
}

function getSourceBg(source) {
  const bg = {
    payment: '#EFF6FF',
    checkout: '#FAF5FF',
    subscription: '#F0FDF4',
    invoice: '#FEF3C7'
  };
  return bg[source] || '#F1F5F9';
}

function getSourceColor(source) {
  const col = {
    payment: '#1E40AF',
    checkout: '#7E22CE',
    subscription: '#15803D',
    invoice: '#92400E'
  };
  return col[source] || '#475569';
}

// ── AI Decision Feed ───────────────────────────────────
function renderFeed() {
  const el = document.getElementById('feed-list');
  if (feedEvents.length === 0) {
    loadFeedFromAudit();
    return;
  }
  if (!feedEvents.length) {
    el.innerHTML = `<div class="empty-state"><div class="empty-icon">🤖</div><div class="empty-title">No decisions yet</div><div class="empty-sub">Run the recovery agent to see AI reasoning</div></div>`;
    return;
  }
  renderFeedItems(feedEvents);
}

async function loadFeedFromAudit() {
  try {
    const d = await apiFetch('/api/audit?limit=300');
    const trail = d.audit_trail || [];
    const actionEvents = trail.filter(e =>
      ['AUTO_RETRY','PAYMENT_LINK_SENT','ESCALATED','STOP_RULE_APPLIED','EMI_OFFER_SENT','SEND_REMINDER','VOICE_CALL'].includes(e.action)
    );
    if (actionEvents.length) {
      renderFeedFromAudit(actionEvents, trail);
    } else {
      document.getElementById('feed-list').innerHTML = `<div class="empty-state"><div class="empty-icon">🤖</div><div class="empty-title">No AI decisions yet</div><div class="empty-sub">Run the recovery agent to see AI reasoning</div></div>`;
    }
  } catch (e) {
    console.warn('Feed:', e.message);
  }
}

function renderFeedFromAudit(events, allTrail) {
  const el = document.getElementById('feed-list');
  el.innerHTML = events.map(e => {
    const meta = e.metadata || {};
    const classification = e.classification || meta.classification || 'UNKNOWN';
    const outcome = e.outcome || '';
    const icon = { AUTO_RETRY: '🔄', PAYMENT_LINK_SENT: '🔗', ESCALATED: '🚨', STOP_RULE_APPLIED: '🛑', EMI_OFFER_SENT: '💳', SEND_REMINDER: '✉️', VOICE_CALL: '📞' }[e.action] || '⚡';
    const iconClass = { AUTO_RETRY:'feed-icon-retry', PAYMENT_LINK_SENT:'feed-icon-link', ESCALATED:'feed-icon-escalate', STOP_RULE_APPLIED:'feed-icon-stop', EMI_OFFER_SENT:'feed-icon-emi', SEND_REMINDER:'feed-icon-link', VOICE_CALL:'feed-icon-escalate' }[e.action] || 'feed-icon-retry';
    const actionLabel = e.action;
    const outcomeClass = { SUCCESS:'tag-success', LINK_SENT_PENDING:'tag-pending', ESCALATED:'tag-escalated', RETRY_FAILED:'tag-failed', EMI_PENDING:'tag-pending', ERROR:'tag-failed', BLOCKED: 'tag-failed', REMINDER_SENT: 'tag-success' }[outcome] || 'tag-pending';
    const amt = e.amount_recovered || 0;

    return `
      <div class="feed-item" onclick="openAuditModal('${e.transaction_id}')">
        <div class="feed-icon ${iconClass}">${icon}</div>
        <div class="feed-content">
          <div class="feed-header">
            <span class="feed-customer">${(e.transaction_id || '').slice(0,16)}...</span>
            <span class="feed-tag" style="background: ${getSourceBg(e.source_type)}; color: ${getSourceColor(e.source_type)}; border:none; margin-left:0.5rem; font-size:0.7rem; font-weight:700;">${e.source_type ? e.source_type.toUpperCase() : 'PAYMENT'}</span>
            <span class="feed-time">${fmtDate(e.timestamp)}</span>
          </div>
          <div class="feed-flow">
            <span class="feed-tag tag-classified">${classification}</span>
            <span class="feed-arrow">→</span>
            <span class="feed-tag tag-action">${actionLabel}</span>
            <span class="feed-arrow">→</span>
            <span class="feed-tag ${outcomeClass}">${outcome}</span>
            ${amt > 0 ? `<span class="feed-arrow">→</span><span style="font-size:0.78rem;font-weight:700;color:#16A34A">₹${fmtINR(amt/100)}</span>` : ''}
          </div>
          <div class="feed-reasoning">${e.reasoning || ''}</div>
          ${meta.explanation_summary ? `<div style="font-size:0.78rem; color:#475569; font-weight:600; margin-top:0.25rem;">💡 Explanation: ${meta.explanation_summary}</div>` : ''}
          <div class="feed-meta">
            ${e.confidence ? `<span class="feed-confidence">Confidence: ${(e.confidence * 100).toFixed(0)}%</span>` : ''}
          </div>
        </div>
      </div>`;
  }).join('');
}

function renderFeedItems(results) {
  const el = document.getElementById('feed-list');
  const txnMap = {};
  allTransactions.forEach(t => { txnMap[t.id] = t; });

  el.innerHTML = results.map(r => {
    const txn = txnMap[r.transaction_id] || {};
    const icon = { AUTO_RETRY:'🔄', PAYMENT_LINK_SENT:'🔗', ESCALATED:'🚨', STOP_RULE_APPLIED:'🛑', EMI_OFFER_SENT:'💳', SEND_REMINDER: '✉️', VOICE_CALL: '📞' }[r.action] || '⚡';
    const iconClass = { AUTO_RETRY:'feed-icon-retry', PAYMENT_LINK_SENT:'feed-icon-link', ESCALATED:'feed-icon-escalate', STOP_RULE_APPLIED:'feed-icon-stop', EMI_OFFER_SENT:'feed-icon-emi', SEND_REMINDER:'feed-icon-link', VOICE_CALL:'feed-icon-escalate' }[r.action] || 'feed-icon-retry';
    const outcomeClass = { SUCCESS:'tag-success', LINK_SENT_PENDING:'tag-pending', ESCALATED:'tag-escalated', RETRY_FAILED:'tag-failed', EMI_PENDING:'tag-pending', BLOCKED: 'tag-failed', REMINDER_SENT: 'tag-success' }[r.outcome] || 'tag-pending';
    const amt = r.amount_recovered || 0;
    const classification = r.classification || 'UNKNOWN';

    return `
      <div class="feed-item" onclick="openModal('${r.transaction_id}')">
        <div class="feed-icon ${iconClass}">${icon}</div>
        <div class="feed-content">
          <div class="feed-header">
            <span class="feed-customer">${txn.customer_name || r.transaction_id.slice(0,14) + '...'}</span>
            <span class="feed-tag" style="background: ${getSourceBg(r.source_type || txn.source_type)}; color: ${getSourceColor(r.source_type || txn.source_type)}; border:none; margin-left:0.5rem; font-size:0.7rem; font-weight:700;">${(r.source_type || txn.source_type || 'payment').toUpperCase()}</span>
            ${txn.amount ? `<span class="feed-amount" style="margin-left:auto">₹${fmtINR(txn.amount/100)}</span>` : ''}
          </div>
          <div class="feed-flow">
            <span class="feed-tag tag-classified">${classification}</span>
            <span class="feed-arrow">→</span>
            <span class="feed-tag tag-action">${r.action}</span>
            <span class="feed-arrow">→</span>
            <span class="feed-tag ${outcomeClass}">${r.outcome}</span>
            ${amt > 0 ? `<span class="feed-arrow">→</span><span style="font-size:0.78rem;font-weight:700;color:#16A34A">₹${fmtINR(amt/100)}</span>` : ''}
          </div>
          <div class="feed-reasoning">${r.reasoning || ''}</div>
          ${r.explanation_summary ? `<div style="font-size:0.78rem; color:#475569; font-weight:600; margin-top:0.25rem;">💡 Explanation: ${r.explanation_summary}</div>` : ''}
          ${r.hinglish_message ? `<div class="feed-hinglish">💬 ${r.hinglish_message}</div>` : ''}
          <div class="feed-meta">
            ${r.confidence ? `<span class="feed-confidence">AI Confidence: ${(r.confidence*100).toFixed(0)}%</span>` : ''}
            ${amt > 0 ? `<span class="feed-recovered-amt">+₹${fmtINR(amt/100)} recovered</span>` : ''}
          </div>
        </div>
      </div>`;
  }).join('');
}

// ── Compliance Panel ───────────────────────────────────
async function loadCompliance() {
  try {
    const d = await apiFetch('/api/compliance');
    document.getElementById('c-fraud').textContent = (d.dnc_blocked_count || 0) + (d.fraud_flagged_count || 0);
    document.getElementById('c-escalated').textContent = d.escalations || 0;
    document.getElementById('c-stopped').textContent = d.stop_rules_applied || 0;

    const p2p = await apiFetch('/api/promise-to-pay');
    document.getElementById('c-p2p').textContent = (p2p.promises || []).length;

    const events = d.compliance_events || [];
    const el = document.getElementById('compliance-events');
    if (!events.length) { el.innerHTML = '<div class="empty-hint">No compliance events yet</div>'; return; }
    el.innerHTML = events.map(e => `
      <div class="compliance-event-row">
        <div class="ce-badge">
          <span class="badge badge-ESCALATED" style="background:${e.action === 'COMPLIANCE_BLOCK' ? '#FEE2E2' : '#FEF3C7'}; color:${e.action === 'COMPLIANCE_BLOCK' ? '#991B1B' : '#92400E'}">
            ${e.action}
          </span>
        </div>
        <div class="ce-reasoning">${e.reasoning || ''}</div>
        <div class="ce-time">${fmtDate(e.timestamp)}</div>
      </div>`).join('');
  } catch (e) { console.warn('Compliance:', e.message); }
}

// ── Audit Trail ────────────────────────────────────────
async function loadAuditTrail() {
  try {
    const d = await apiFetch('/api/audit?limit=300');
    const entries = d.audit_trail || [];
    document.getElementById('audit-count-badge').textContent = entries.length + ' events';
    const el = document.getElementById('audit-list');
    if (!entries.length) {
      el.innerHTML = `<div class="empty-state" style="padding:3rem"><div class="empty-icon">📋</div><div class="empty-title">Audit trail will appear after recovery runs</div></div>`;
      return;
    }
    el.innerHTML = entries.map((e, i) => {
      const meta = e.metadata || {};
      const dotClass = `audit-dot-${e.action}`;
      const outcomeClass = `audit-outcome-${e.outcome}`;
      const amt = e.amount_recovered || 0;
      return `
        <div class="audit-row">
          <div class="audit-timeline">
            <div class="audit-dot ${dotClass}" style="background:${e.action === 'VOICE_CALL' ? '#7C3AED' : (e.action === 'SEND_REMINDER' ? '#6366F1' : '')}"></div>
            ${i < entries.length - 1 ? '<div class="audit-line"></div>' : ''}
          </div>
          <div class="audit-content">
            <div class="audit-meta">
              <span class="audit-action-tag">${e.action}</span>
              <span class="audit-txn-id">${(e.transaction_id||'').slice(0,18)}...</span>
              <span class="feed-tag" style="background: ${getSourceBg(e.source_type)}; color: ${getSourceColor(e.source_type)}; border:none; font-size:0.68rem; font-weight:700;">${(e.source_type || 'payment').toUpperCase()}</span>
              ${e.classification ? `<span class="feed-tag tag-classified" style="font-size:0.68rem; margin-left:0.5rem;">${e.classification}</span>` : ''}
              <span class="audit-outcome-tag ${outcomeClass}">${e.outcome}</span>
              <span class="audit-time">${fmtDate(e.timestamp)}</span>
            </div>
            <div class="audit-reasoning">${e.reasoning || ''}</div>
            ${amt > 0 ? `<div class="audit-amount">+₹${fmtINR(amt/100)} recovered</div>` : ''}
            ${meta.explanation_summary ? `<div style="font-size:0.75rem; color:#475569; font-weight:600; margin-top:0.25rem;">💡 Explanation: ${meta.explanation_summary}</div>` : ''}
          </div>
        </div>`;
    }).join('');
  } catch (e) { console.warn('Audit:', e.message); }
}

// ── Modal ──────────────────────────────────────────────
async function openModal(txnId) {
  const txn = allTransactions.find(t => t.id === txnId);
  if (!txn) return;
  document.getElementById('modal-title').textContent = (txn.customer_name || 'Unknown') + ' — Detail View';

  let auditHtml = '<div style="margin-top:1.25rem"><div class="detail-key" style="margin-bottom:0.6rem">Audit Trail</div>';
  try {
    const d = await apiFetch(`/api/transactions/${txnId}/audit`);
    const trail = d.audit_trail || [];
    auditHtml += trail.map(e => `
      <div style="padding:0.75rem;background:var(--bg);border-radius:6px;margin-bottom:0.5rem;border:1px solid var(--border)">
        <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem">
          <span class="audit-action-tag">${e.action}</span>
          ${e.classification ? `<span class="feed-tag tag-classified" style="font-size:0.68rem">${e.classification}</span>` : ''}
          <span class="audit-outcome-tag audit-outcome-${e.outcome}">${e.outcome}</span>
          ${e.amount_recovered > 0 ? `<span style="color:var(--success);font-size:0.75rem;font-weight:700">+₹${fmtINR(e.amount_recovered/100)}</span>` : ''}
          <span class="audit-time" style="margin-left:auto">${fmtDate(e.timestamp)}</span>
        </div>
        <div style="font-size:0.8rem;color:var(--text-secondary);line-height:1.5">${e.reasoning||''}</div>
      </div>`).join('');
  } catch {}
  auditHtml += '</div>';

  const hinglish = txn.hinglish_message;
  document.getElementById('modal-body').innerHTML = `
    <div class="detail-grid">
      <div><div class="detail-key">Entity ID</div><div class="detail-val mono">${txn.id}</div></div>
      <div><div class="detail-key">Amount</div><div class="detail-val">₹${fmtINR((txn.amount||0)/100)}</div></div>
      <div><div class="detail-key">Customer</div><div class="detail-val">${txn.customer_name||'—'}</div></div>
      <div><div class="detail-key">Status</div><div class="detail-val"><span class="badge badge-${txn.status}">${txn.status}</span></div></div>
      <div><div class="detail-key">Failure Type</div><div class="detail-val"><span class="action-tag">${txn.failure_type||'—'}</span></div></div>
      <div><div class="detail-key">Attempts</div><div class="detail-val">${txn.attempts||0} / 3</div></div>
      <div><div class="detail-key">Email</div><div class="detail-val" style="font-size:0.82rem">${txn.customer_email||'—'}</div></div>
      <div><div class="detail-key">Source Type</div><div class="detail-val" style="text-transform:uppercase; font-weight:700; color:${getSourceColor(txn.source_type)}">${txn.source_type || 'payment'}</div></div>
    </div>
    <div style="margin-bottom:1rem;padding:0.75rem;background:#FEF2F2;border-radius:6px;border-left:3px solid #DC2626">
      <div class="detail-key" style="margin-bottom:0.25rem">Failure Reason</div>
      <div style="font-size:0.85rem;color:var(--text-secondary)">${txn.failure_reason||'—'}</div>
    </div>
    ${hinglish ? `<div class="hinglish-box"><div class="hinglish-label">🇮🇳 Recovery Message (Hinglish)</div>${hinglish}</div>` : ''}
    ${txn.status !== 'RECOVERED' && txn.status !== 'PAID' && txn.status !== 'ACTIVE' ? `
      <button class="btn btn-primary" onclick="startVoiceCall('${txn.id}', '${txn.customer_phone || '+91 98765 43210'}', \`${hinglish || 'Hello, payment fail ho gaya hai. Please link check karke payment complete karein.'}\`)" style="background: #7C3AED; width: 100%; margin-top: 1rem; border-color: #7C3AED; justify-content: center; font-weight: 700;">
        📞 Start Voice Recovery Call
      </button>
    ` : ''}
    ${auditHtml}`;
  document.getElementById('modal-overlay').classList.add('open');
}

async function openAuditModal(txnId) {
  const txn = allTransactions.find(t => t.id === txnId) || { id: txnId };
  await openModal(txnId);
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
}

// ── Voice Agent Call Simulation ────────────────────────

function startVoiceCall(id, phone, script) {
  document.getElementById('voice-call-status').textContent = 'Dialing customer...';
  document.getElementById('voice-call-contact').textContent = phone;
  document.getElementById('voice-script-box').textContent = `"${script}"`;
  document.getElementById('voice-modal-overlay').classList.add('open');
  
  setTimeout(() => {
    document.getElementById('voice-call-status').textContent = '🔊 Connected (Speaking Hinglish Script...)';
    
    if (synth) {
      synth.cancel(); 
      voiceUtterance = new SpeechSynthesisUtterance(script);
      voiceUtterance.lang = 'hi-IN'; // Indian Accent
      voiceUtterance.rate = 0.9;
      
      voiceUtterance.onend = async () => {
        document.getElementById('voice-call-status').textContent = '✅ Call Complete (Customer promised to pay)';
        showToast('Voice recovery call complete! Status updating...', 'success');
        
        // Trigger actual recovery status change on backend
        try {
          await apiFetch(`/api/recover/${id}`, { method: 'POST' });
          await loadTransactionTable();
          await loadStats();
          await loadAuditTrail();
        } catch (e) {
          console.warn('Recover update failed:', e.message);
        }
      };
      voiceUtterance.onerror = () => {
        document.getElementById('voice-call-status').textContent = '✅ Call Complete (Simulation)';
      };
      
      synth.speak(voiceUtterance);
    } else {
      setTimeout(async () => {
        document.getElementById('voice-call-status').textContent = '✅ Call Complete (Simulation)';
        try {
          await apiFetch(`/api/recover/${id}`, { method: 'POST' });
          await loadTransactionTable();
          await loadStats();
          await loadAuditTrail();
        } catch (e) {}
      }, 5000);
    }
  }, 1500);
}

function stopVoiceCall() {
  if (synth) {
    synth.cancel();
  }
  document.getElementById('voice-modal-overlay').classList.remove('open');
}

function closeVoiceModal() {
  stopVoiceCall();
}

// ── Utilities ──────────────────────────────────────────
async function apiFetch(path, options = {}) {
  const token = sessionStorage.getItem("reclaim_token");
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {})
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(API + path, {
    ...options,
    headers
  });
  if (res.status === 401) {
    sessionStorage.removeItem("reclaim_token");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function fmtINR(n) {
  if (!n && n !== 0) return '0';
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Math.round(n));
}

function fmtTime(seconds) {
  if (!seconds || seconds === 0) return '—';
  if (seconds < 60) return Math.round(seconds) + 's';
  if (seconds < 3600) return Math.round(seconds / 60) + 'm';
  return (seconds / 3600).toFixed(1) + 'h';
}

function fmtDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleString('en-IN', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });
  } catch { return iso; }
}

function fmtLabel(key) {
  return (key || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function setMetric(id, val) {
  const el = document.getElementById(id);
  if (!el || el.textContent === String(val)) return;
  el.textContent = val;
  el.classList.remove('count-pop');
  void el.offsetWidth;
  el.classList.add('count-pop');
}

function showToast(msg, type = 'info') {
  const stack = document.getElementById('toast-stack');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  stack.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.3s'; setTimeout(() => el.remove(), 300); }, 4500);
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeModal(); closeVoiceModal(); } });
