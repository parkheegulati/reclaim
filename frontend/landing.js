/* ═══════════════════════════════════════════════════════
   reclaim. — Landing Page JavaScript with Auth Handlers
   ═══════════════════════════════════════════════════════ */

const API = 'http://localhost:8000';

document.addEventListener('DOMContentLoaded', () => {
  loadLiveStats();
  loadLiveAuditPreview();
  startTerminalSimulation();

  // Bind CTA buttons to check auth before navigating
  const ctas = document.querySelectorAll('.nav-cta, .btn-hero-primary, .btn-hero-primary.btn-large');
  ctas.forEach(cta => {
    cta.addEventListener('click', (e) => {
      const token = sessionStorage.getItem('reclaim_token');
      if (!token) {
        e.preventDefault();
        openAuthModal();
      }
    });
  });

  const btnClose = document.getElementById('btn-close-auth');
  if (btnClose) btnClose.addEventListener('click', closeAuthModal);
});

function openAuthModal() {
  document.getElementById('auth-modal').classList.add('active');
  document.getElementById('auth-error').style.display = 'none';
}

function closeAuthModal() {
  document.getElementById('auth-modal').classList.remove('active');
}

function toggleAuthTab(tab) {
  const isLogin = tab === 'login';
  document.getElementById('tab-login').classList.toggle('active', isLogin);
  document.getElementById('tab-register').classList.toggle('active', !isLogin);
  document.getElementById('form-login').style.display = isLogin ? 'block' : 'none';
  document.getElementById('form-register').style.display = isLogin ? 'none' : 'block';
  document.getElementById('auth-subtitle').textContent = isLogin ? 'Log in to view your dashboard' : 'Create a new merchant profile';
  document.getElementById('auth-error').style.display = 'none';
}

async function handleAuthSubmit(event, action) {
  event.preventDefault();
  const errorDiv = document.getElementById('auth-error');
  errorDiv.style.display = 'none';

  try {
    let res;
    if (action === 'login') {
      const email = document.getElementById('login-email').value;
      const password = document.getElementById('login-password').value;
      
      const formData = new URLSearchParams();
      formData.append('username', email);
      formData.append('password', password);

      res = await fetch(`${API}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: formData
      });
    } else {
      const name = document.getElementById('reg-name').value;
      const email = document.getElementById('reg-email').value;
      const password = document.getElementById('reg-password').value;

      res = await fetch(`${API}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, password })
      });
    }

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Authentication failed');
    }

    // Save token
    sessionStorage.setItem('reclaim_token', data.access_token);
    
    // Redirect to dashboard
    window.location.href = '/dashboard';
  } catch (err) {
    errorDiv.textContent = err.message;
    errorDiv.style.display = 'block';
  }
}

// Helper for formatting INR currency
function fmtINR(n) {
  if (!n && n !== 0) return '0';
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Math.round(n));
}

// Fetch stats and update all counters on landing page
async function loadLiveStats() {
  try {
    const token = sessionStorage.getItem('reclaim_token');
    if (!token) return; // Do not fetch stats if unauthenticated

    const res = await fetch(`${API}/api/stats`, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) throw new Error('API Offline or Unauthorized');
    const s = await res.json();

    const recoveredAmt = s.amount_recovered / 100;
    const atRiskAmt = s.amount_at_risk / 100;

    // Update Hero Stats Strip
    updateCounter('hs-recovered', `₹${fmtINR(recoveredAmt)}`);
    updateCounter('hs-rate', `${s.recovery_rate}%`);
    updateCounter('hs-txns', s.total_transactions);
    updateCounter('hs-time', s.avg_recovery_time_seconds ? `${Math.round(s.avg_recovery_time_seconds / 60)}m` : '—');

    // Update Metrics Showcase Section
    updateCounter('ms-recovered', `₹${fmtINR(recoveredAmt)}`);
    updateCounter('ms-rate', `${s.recovery_rate}%`);
    updateCounter('ms-retries', s.retries_attempted);
    updateCounter('ms-links', s.links_sent);
    updateCounter('ms-time', s.avg_recovery_time_seconds ? `${Math.round(s.avg_recovery_time_seconds / 60)}m` : '—');
    updateCounter('ms-stopped', s.stop_rules_applied);

    document.getElementById('ms-rate-sub').textContent = `across ${s.total_transactions} failed payments`;

  } catch (e) {
    console.warn('Could not load live stats:', e.message);
  }
}

function updateCounter(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = value;
  el.classList.remove('count-anim');
  void el.offsetWidth; // Trigger reflow
  el.classList.add('count-anim');
}

// Fetch actual audit events and style them for the landing page preview card
async function loadLiveAuditPreview() {
  try {
    let events = [];
    const token = sessionStorage.getItem('reclaim_token');
    if (token) {
      try {
        const res = await fetch(`${API}/api/audit?limit=8`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.ok) {
          const d = await res.json();
          events = d.audit_trail || [];
        }
      } catch (err) {
        console.warn("Failed to load live audit:", err);
      }
    }

    // Fallback to high-fidelity mock events for anonymous/first-time visitors
    if (!events || !events.length) {
      events = [
        {
          transaction_id: "pay_det_0024",
          action: "DIAGNOSIS",
          classification: "USER_ERROR",
          outcome: "DIAGNOSED",
          reasoning: "Payment pay_det_0024 failed due to WRONG_CVV. USER_ERROR detected. Gemini AI recommends PAYMENT_LINK.",
          amount_recovered: 0,
          timestamp: new Date(Date.now() - 40000).toISOString()
        },
        {
          transaction_id: "pay_det_0024",
          action: "PAYMENT_LINK",
          classification: "USER_ERROR",
          outcome: "SUCCESS",
          reasoning: "Payment recovery link dispatched via WhatsApp nudge. Customer Priya Patel successfully paid. SUCCESS.",
          amount_recovered: 299900,
          timestamp: new Date(Date.now() - 32000).toISOString()
        },
        {
          transaction_id: "pay_det_0005",
          action: "STOP_RULE_APPLIED",
          classification: "ECONOMIC_LIMIT",
          outcome: "EXHAUSTED",
          reasoning: "Stop rule ECONOMIC_LIMIT triggered: transaction amount (₹5.00) below ₹10 threshold. Workflow stopped.",
          amount_recovered: 0,
          timestamp: new Date(Date.now() - 120000).toISOString()
        },
        {
          transaction_id: "pay_det_0019",
          action: "AUTO_RETRY",
          classification: "BANK_DOWNTIME",
          outcome: "SUCCESS",
          reasoning: "Issuing bank online. Idempotent AUTO_RETRY succeeded. SUCCESS.",
          amount_recovered: 49900,
          timestamp: new Date(Date.now() - 180000).toISOString()
        },
        {
          transaction_id: "pay_det_0045",
          action: "COMPLIANCE_BLOCK",
          classification: "COOLDOWN_BLOCK",
          outcome: "BLOCKED",
          reasoning: "COMPLIANCE_VIOLATION: Minimum cooldown window of 30m not met. Outreach blocked. BLOCKED.",
          amount_recovered: 0,
          timestamp: new Date(Date.now() - 250000).toISOString()
        }
      ];
    }

    const container = document.getElementById('ap-events');
    if (!container) return;

    container.innerHTML = events.map(e => {
      let styledReason = e.reasoning || '';
      // Inject highlight classes for landing preview
      if (e.classification) {
        styledReason = styledReason.replace(new RegExp(e.classification, 'g'), `<span class="hl-class">${e.classification}</span>`);
      }
      if (e.action) {
        styledReason = styledReason.replace(new RegExp(e.action, 'g'), `<span class="hl-action">${e.action}</span>`);
      }
      if (e.outcome === 'SUCCESS') {
        styledReason = styledReason.replace('SUCCESS', '<span class="hl-success">SUCCESS</span>');
      } else if (e.outcome === 'ESCALATED' || e.outcome === 'ESCALATE') {
        styledReason = styledReason.replace('ESCALATE', '<span class="hl-escalate">ESCALATE</span>');
      }

      const formattedTime = new Date(e.timestamp + (e.timestamp.endsWith('Z') ? '' : 'Z')).toLocaleTimeString('en-IN', {
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      });

      return `
        <div class="ap-event">
          <div class="ap-event-text">${styledReason}</div>
          ${e.amount_recovered > 0 ? `<div class="ap-event-amount">+₹${fmtINR(e.amount_recovered / 100)} Recovered</div>` : ''}
          <div class="ap-event-time">${formattedTime} · Txn: ${e.transaction_id.slice(0, 12)}...</div>
        </div>
      `;
    }).join('');

  } catch (e) {
    const container = document.getElementById('ap-events');
    if (container) {
      container.innerHTML = '<div class="ap-empty">Audit log empty. Launch the dashboard to run your first recovery batch.</div>';
    }
  }
}

// Hero log simulator
function startTerminalSimulation() {
  const terminal = document.getElementById('flow-body');
  if (!terminal) return;

  const logs = [
    { text: 'Listening to Razorpay webhook fails...', class: 'fe-muted' },
    { text: '⚠️ [pay_fa5adc4e9d05] failed: GATEWAY_ERROR (Issuing bank servers offline)', class: 'fe-warn' },
    { text: '🧠 Gemini AI: Diagnosed BANK_DOWNTIME (confidence 94%)', class: 'fe-info' },
    { text: '⚡ Scheduling auto-retry with idempotency in 10 mins (cooldown mode)...', class: 'fe-muted' },
    { text: '⚠️ [pay_ca2df357137c] failed: BAD_REQUEST_ERROR (Incorrect CVV entered)', class: 'fe-warn' },
    { text: '🧠 Gemini AI: Diagnosed USER_ERROR (Wrong CVV, confidence 88%)', class: 'fe-info' },
    { text: '🔗 Creating Razorpay Payment Link for ₹4,500...', class: 'fe-muted' },
    { text: '💬 Sending Hinglish message: "Card ka CVV galat tha..."', class: 'fe-info' },
    { text: '🔒 [pay_6f6045077f91] failed: FRAUD_FLAGGED', class: 'fe-warn' },
    { text: '🛡️ Stop Rule Enforced: FRAUD_FLAGGED cannot be auto-recovered.', class: 'fe-warn' },
    { text: '🚨 Escalating to Risk Operations Team & creating Promise-to-Pay...', class: 'fe-warn' },
    { text: '🔄 Retrying pay_fa5adc4e9d05...', class: 'fe-muted' },
    { text: '✅ pay_fa5adc4e9d05 retry captured successfully!', class: 'fe-success' },
    { text: '💰 Revenue Recovered: <span class="fe-amount">₹2,542</span>', class: 'fe-success' },
  ];

  let lineIdx = 0;
  function printNextLine() {
    if (lineIdx >= logs.length) {
      setTimeout(() => {
        terminal.innerHTML = '';
        lineIdx = 0;
        printNextLine();
      }, 5000);
      return;
    }

    const time = new Date().toLocaleTimeString('en-IN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const log = logs[lineIdx];
    const el = document.createElement('div');
    el.className = 'fe-line';
    el.innerHTML = `
      <span class="fe-time">${time}</span>
      <span class="fe-icon">&gt;</span>
      <span class="fe-content ${log.class}">${log.text}</span>
    `;
    terminal.appendChild(el);
    terminal.scrollTop = terminal.scrollHeight;

    lineIdx++;
    const nextDelay = lineIdx === 12 || lineIdx === 13 ? 1500 : Math.random() * 800 + 400;
    setTimeout(printNextLine, nextDelay);
  }

  printNextLine();
}
