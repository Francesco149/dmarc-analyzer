let reports = [];
let activeIdx = null;
let isDark = false;

// ── theme ────────────────────────────────────────────────────────────────────
function getSystemTheme() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

// Set initial theme from system preference
document.documentElement.setAttribute('data-theme', getSystemTheme());
document.getElementById('theme-btn').textContent = getSystemTheme() === 'dark' ? '☀ Light' : '☽ Dark';

// Also update if system theme changes while page is open
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
  if (!document.documentElement.hasAttribute('data-theme-override')) {
    document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
  }
});

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  document.documentElement.setAttribute('data-theme-override', '1');
  document.getElementById('theme-btn').textContent = next === 'dark' ? '☀ Light' : '☽ Dark';
}

// ── drag and drop ─────────────────────────────────────────────────────────────
const overlay = document.getElementById('drop-overlay');
let dragCounter = 0;
document.addEventListener('dragenter', e => { dragCounter++; overlay.classList.add('visible'); });
document.addEventListener('dragleave', e => { if(--dragCounter <= 0) overlay.classList.remove('visible'); });
document.addEventListener('dragover',  e => e.preventDefault());
document.addEventListener('drop', async e => {
  e.preventDefault(); dragCounter = 0; overlay.classList.remove('visible');
  for (const file of e.dataTransfer.files) await handleFile(file);
});
document.getElementById('file-input').addEventListener('change', async e => {
  for (const file of e.target.files) await handleFile(file);
  e.target.value = '';
});

// ── file handling ─────────────────────────────────────────────────────────────
async function handleFile(file) {
  const name = file.name.toLowerCase();
  if (name === 'reports.json' || name.endsWith('.json')) {
    const text = await file.text();
    try {
      const feed = JSON.parse(text);
      const reps = feed.reports || feed;
      reps.forEach(r => addReport(r));
      renderFeed();
    } catch(e) { alert('Could not parse JSON feed: ' + e.message); }
    return;
  }
  let xmlBytes;
  if (name.endsWith('.zip')) {
    try {
      const ab = await file.arrayBuffer();
      const zip = await JSZip.loadAsync(ab);
      const xmlName = Object.keys(zip.files).find(n => n.toLowerCase().endsWith('.xml'));
      if (!xmlName) { alert('No XML found in zip.'); return; }
      xmlBytes = await zip.files[xmlName].async('uint8array');
      xmlBytes = new TextDecoder().decode(xmlBytes);
    } catch(e) { alert('Could not read zip: ' + e.message); return; }
  } else if (name.endsWith('.xml')) {
    xmlBytes = await file.text();
  } else { return; }

  let report;
  try {
    report = parseDmarcXml(xmlBytes);
  } catch(e) { alert('Could not parse DMARC XML: ' + e.message); return; }
  addReport(report);
  renderFeed();
  selectReport(reports.length - 1);
}

// ── auto-fetch feed ───────────────────────────────────────────────────────────
async function tryFetchFeed() {
  try {
    const resp = await fetch('data/reports.json', {cache:'no-store'});
    if (!resp.ok) return;
    const feed = await resp.json();
    const reps = feed.reports || feed;
    reps.forEach(r => addReport(r));
    renderFeed();
    if (reports.length > 0) selectReport(0);
  } catch(e) {}
}

// ── XML parser ────────────────────────────────────────────────────────────────
function gx(el, sel, def='') {
  const node = el.querySelector(sel);
  return node?.textContent?.trim() || def;
}
function gxa(el, sel) { return Array.from(el.querySelectorAll(sel)); }

const KNOWN_ORGS = [
  [/^(209\.85\.|74\.125\.|64\.233\.|66\.249\.|72\.14\.)/, 'Google'],
  [/^(149\.72\.|167\.89\.|208\.117\.|198\.21\.)/, 'SendGrid'],
  [/^(54\.|52\.|35\.|18\.)/, 'AWS'],
  [/^(148\.163\.|198\.2\.|205\.201\.)/, 'Mailchimp'],
  [/^(40\.|13\.|20\.)/, 'Microsoft'],
  [/^(185\.12\.80\.|199\.255\.192\.)/, 'Postmark'],
];
function identifyOrg(ip) {
  for (const [re, name] of KNOWN_ORGS) if (re.test(ip)) return name;
  return null;
}

function fmtDate(ts) {
  if (!ts) return '';
  try { return new Date(parseInt(ts)*1000).toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric'}); }
  catch { return ''; }
}

function parseDmarcXml(xml) {
  const doc = new DOMParser().parseFromString(xml, 'text/xml');
  if (doc.querySelector('parsererror')) throw new Error('Invalid XML');

  const meta   = doc.querySelector('report_metadata');
  const policy = doc.querySelector('policy_published');
  if (!meta || !policy) throw new Error('Missing DMARC sections');

  const begin = gx(meta, 'date_range > begin');
  const end   = gx(meta, 'date_range > end');

  const report = {
    report_id:  gx(meta, 'report_id'),
    submitter:  gx(meta, 'org_name'),
    domain:     gx(policy, 'domain'),
    policy:     gx(policy, 'p') || 'none',
    sp:         gx(policy, 'sp') || gx(policy, 'p') || 'none',
    pct:        gx(policy, 'pct') || '100',
    adkim:      gx(policy, 'adkim') || 'r',
    aspf:       gx(policy, 'aspf')  || 'r',
    date_begin: fmtDate(begin),
    date_end:   fmtDate(end),
    begin_ts:   begin,
    end_ts:     end,
    records:    [],
    total: 0, passed: 0, failed: 0,
  };

  for (const rec of gxa(doc, 'record')) {
    const row = rec.querySelector('row');
    if (!row) continue;
    const ip    = gx(row, 'source_ip');
    const count = parseInt(gx(row, 'count') || '0');
    const pe    = row.querySelector('policy_evaluated');
    const dkim  = gx(pe, 'dkim') || 'fail';
    const spf   = gx(pe, 'spf')  || 'fail';
    const disp  = gx(pe, 'disposition') || 'none';
    const pass  = dkim === 'pass' || spf === 'pass';
    report.total  += count;
    if (pass) report.passed += count; else report.failed += count;
    report.records.push({ source_ip: ip, org: identifyOrg(ip), count, disposition: disp, dkim, spf, dmarc_pass: pass });
  }

  report.pass_rate = report.total > 0 ? Math.round((report.passed/report.total)*100) : 0;
  return report;
}

// ── report store ──────────────────────────────────────────────────────────────
const seenIds = new Set();
function addReport(r) {
  const id = r.report_id || (r.begin_ts + r.submitter);
  if (seenIds.has(id)) return;
  seenIds.add(id);
  reports.push(r);
  reports.sort((a,b) => (b.end_ts||0) > (a.end_ts||0) ? 1 : -1);
}

// ── sidebar feed ──────────────────────────────────────────────────────────────
function pill(val) {
  const v = (val||'').toLowerCase();
  const cls = {pass:'pass',fail:'fail',none:'none',quarantine:'quarantine',reject:'reject'}[v]||'none';
  return `<span class="pill pill-${cls}">${v||'—'}</span>`;
}

function renderFeed() {
  const el = document.getElementById('feed-list');
  if (reports.length === 0) return;
  const empty = document.getElementById('feed-empty');
  if (empty) empty.remove();
  el.innerHTML = reports.map((r, i) => {
    const rate = r.pass_rate ?? 100;
    const color = rate === 100 ? 'var(--success)' : rate >= 80 ? 'var(--warn)' : 'var(--danger)';
    return `<div class="feed-item ${activeIdx===i?'active':''}" onclick="selectReport(${i})">
      <div class="fi-date">${r.date_end || '—'}</div>
      <div class="fi-domain">${r.domain || '—'}</div>
      <div class="fi-sub">${r.submitter || '—'}</div>
      <div class="fi-bar"><div class="fi-bar-fill" style="width:${rate}%;background:${color}"></div></div>
      <div class="fi-meta"><span>${r.total?.toLocaleString() || 0} messages</span><span style="color:${color}">${rate}% pass</span></div>
    </div>`;
  }).join('');
}

// ── detail view ───────────────────────────────────────────────────────────────
function selectReport(idx) {
  activeIdx = idx;
  renderFeed();
  const r = reports[idx];
  document.getElementById('welcome').style.display = 'none';
  document.getElementById('detail').style.display = 'block';

  const total = r.total || 0;
  const passed = r.passed || 0;
  const failed = r.failed || 0;
  const rate   = r.pass_rate ?? (total > 0 ? Math.round(passed/total*100) : 0);

  const passClass = rate===100?'good':rate>=80?'warn':'bad';
  const failClass = failed===0?'good':'bad';

  let mainBanner = '';
  if (failed === 0)
    mainBanner = `<div class="banner banner-success">&#10003; All ${total.toLocaleString()} messages passed DMARC. Authentication looks healthy.</div>`;
  else if (rate >= 80)
    mainBanner = `<div class="banner banner-warn">&#9888; ${failed.toLocaleString()} message${failed!==1?'s':''} failed. Check highlighted rows — likely a sender missing from SPF or not DKIM-signed.</div>`;
  else
    mainBanner = `<div class="banner banner-danger">&#9888; High failure rate — ${failed} of ${total} failed. Investigate for spoofing or misconfigured senders.</div>`;

  let policyBanner = '';
  const pol = (r.policy||'none').toLowerCase();
  if (pol==='none')        policyBanner = `<div class="banner banner-warn">&#9432; <span><strong>p=none</strong> — monitoring mode only. Failing mail is not blocked. Move to <strong>p=quarantine</strong> once all legitimate senders pass.</span></div>`;
  else if (pol==='quarantine') policyBanner = `<div class="banner banner-info">&#9432; <span><strong>p=quarantine</strong> — failing mail goes to spam. Consider upgrading to <strong>p=reject</strong> when ready.</span></div>`;
  else if (pol==='reject') policyBanner = `<div class="banner banner-success">&#10003; <strong>p=reject</strong> — maximum protection. Failing mail is blocked outright.</div>`;

  const tableRows = (r.records||[]).map(rec => {
    const org = rec.org ? `<span class="org-tag">${rec.org}</span>` : '';
    let vClass, vText;
    if (rec.dkim==='pass'&&rec.spf==='pass') { vClass='verdict-good'; vText='fully aligned'; }
    else if (rec.dkim==='fail'&&rec.spf==='fail') { vClass='verdict-bad'; vText='both failed'; }
    else { vClass='verdict-warn'; vText='partial pass'; }
    const rowClass = rec.dkim==='fail'&&rec.spf==='fail' ? 'row-fail' : '';
    return `<tr class="${rowClass}">
      <td><span class="mono">${rec.source_ip}</span>${org}</td>
      <td style="text-align:right;font-weight:500">${rec.count?.toLocaleString()}</td>
      <td style="text-align:center">${pill(rec.dkim)}</td>
      <td style="text-align:center">${pill(rec.spf)}</td>
      <td style="text-align:center">${pill(rec.disposition)}</td>
      <td><span class="${vClass}">${vText}</span></td>
    </tr>`;
  }).join('');

  const adkimLabel = r.adkim==='s'?'strict':'relaxed';
  const aspfLabel  = r.aspf==='s' ?'strict':'relaxed';

  document.getElementById('detail').innerHTML = `
    <div class="detail-header">
      <div>
        <div class="detail-title">${r.domain || 'Unknown domain'}</div>
        <div class="detail-sub">Submitted by ${r.submitter || '—'} &mdash; ${r.date_begin} to ${r.date_end}</div>
      </div>
    </div>

    <p class="section-label">Overview</p>
    <div class="metrics">
      <div class="metric"><div class="metric-label">Total</div><div class="metric-value">${total.toLocaleString()}</div></div>
      <div class="metric"><div class="metric-label">Passed</div><div class="metric-value good">${passed.toLocaleString()}</div></div>
      <div class="metric"><div class="metric-label">Failed</div><div class="metric-value ${failClass}">${failed.toLocaleString()}</div></div>
      <div class="metric"><div class="metric-label">Pass rate</div><div class="metric-value ${passClass}">${rate}%</div></div>
    </div>
    ${mainBanner}

    <hr class="sep">
    <p class="section-label">Policy &amp; metadata</p>
    <div class="meta-grid">
      <div class="meta-card">
        <div class="meta-row"><span class="meta-key">Report ID</span><span class="meta-val">${r.report_id||'—'}</span></div>
        <div class="meta-row"><span class="meta-key">Submitter</span><span class="meta-val normal">${r.submitter||'—'}</span></div>
        <div class="meta-row"><span class="meta-key">Period</span><span class="meta-val normal">${r.date_begin} – ${r.date_end}</span></div>
      </div>
      <div class="meta-card">
        <div class="meta-row"><span class="meta-key">Policy (p=)</span><span class="meta-val">${pill(r.policy)}</span></div>
        <div class="meta-row"><span class="meta-key">Subdomain (sp=)</span><span class="meta-val">${pill(r.sp)}</span></div>
        <div class="meta-row"><span class="meta-key">Percentage</span><span class="meta-val normal">${r.pct||100}%</span></div>
        <div class="meta-row"><span class="meta-key">DKIM alignment</span><span class="meta-val normal">${adkimLabel}</span></div>
        <div class="meta-row"><span class="meta-key">SPF alignment</span><span class="meta-val normal">${aspfLabel}</span></div>
      </div>
    </div>
    <div style="margin-top:10px">${policyBanner}</div>

    <hr class="sep">
    <p class="section-label">Sending sources (${(r.records||[]).length})</p>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Source IP</th><th style="text-align:right">Messages</th>
          <th style="text-align:center">DKIM</th><th style="text-align:center">SPF</th>
          <th style="text-align:center">Disposition</th><th>Result</th>
        </tr></thead>
        <tbody>${tableRows}</tbody>
      </table>
    </div>`;
}

tryFetchFeed();
