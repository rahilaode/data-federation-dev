'use strict';
/* ASCAM administrator console.
   All dynamic data passes through esc() before it enters the HTML, because the content of
   events, plans, and artifacts comes from other systems and must not be trusted as markup. */

const S = { user: null, obdf: null, oid: null, pending: null, unread: null, types: {}, events: {}, plans: {}, timer: null };
const STEPS = ['deploy_vdb', 'validate', 'start_ontop', 'switch', 'reload_ontop', 'verify', 'sync', 'rollback'];
const STEP_NAMES = {
  deploy_vdb: 'Deploy VDB', validate: 'Validate', start_ontop: 'Start standby Ontop', switch: 'Switch connections', reload_ontop: 'Reload Ontop',
  verify: 'Verify', sync: 'Synchronise', rollback: 'Roll back',
};
const LAYERS = {
  vdb: { name: 'VDB (Σ_S)', cls: 'sigma' },
  r2rml: { name: 'Mapping (ℳ)', cls: 'mapping' },
  ontology: { name: 'Ontology (𝒯)', cls: 'ontology' },
};
const STATUS = {
  pending_approval: ['Waiting for approval', 'decision'], approved: ['Approved', ''],
  executed: ['Applied', 'success'], failed: ['Failed', 'failure'], rejected: ['Rejected', 'failure'],
  superseded: ['Superseded', ''], planned: ['Planned', ''], ignored: ['Ignored', ''],
  received: ['Received', ''], running: ['Running', 'decision'], succeeded: ['Succeeded', 'success'],
  rolled_back: ['Rolled back', 'failure'], active: ['Active', 'success'], candidate: ['Candidate', ''],
  auto: ['Automatic', ''], hitl: ['Needs approval', 'decision'],
  notify: ['Notification', 'warning'], notified: ['Notified', 'warning'], revised: ['Revised', ''],
};
const LINEAGE_LAYERS = [
  { key: 'source', title: 'Source column', cls: 'source' },
  { key: 'foreign', title: 'Foreign table (Σ_S)', cls: 'sigma' },
  { key: 'view', title: 'View (Σ_V)', cls: 'sigma' },
  { key: 'mapping', title: 'TriplesMap (ℳ)', cls: 'mapping' },
  { key: 'ontology', title: 'Property (𝒯)', cls: 'ontology' },
];
const ROLES = {
  literal_value: 'literal value', iri_template: 'IRI template', join_key: 'join key', sql_predicate: 'SQL predicate',
  dynamic_predicate: 'dynamic predicate', projection_only: 'projection only',
  maps_to: 'mapped to property', name_in_source: 'NAMEINSOURCE', direct: 'direct',
  passthrough: 'pass-through', star: 'SELECT *', expression: 'expression', predicate: 'WHERE/JOIN clause',
};

const TARGET_KINDS = {
  teiid_mgmt: 'Teiid management', teiid_odbc: 'Teiid (ODBC)', ontop_sparql: 'Ontop SPARQL endpoint',
  ontop_agent: 'Ontop Agent', kafka: 'Kafka',
};
const COLLISION = { qualify_with_class: 'Add the class name to the IRI', hitl: 'Leave it to the administrator' };

const $ = (sel) => document.querySelector(sel);
const content = () => $('#content');

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
function badge(status) {
  const [text, cls] = STATUS[status] || [status || '-', ''];
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}
function time(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function relative(iso) {
  if (!iso) return '-';
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return time(iso);
}
function duration(ms) {
  if (ms === null || ms === undefined) return '-';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}
function short(iri) {
  if (!iri) return '-';
  const s = String(iri);
  const i = Math.max(s.lastIndexOf('#'), s.lastIndexOf('/'));
  return i >= 0 ? s.slice(i + 1) : s;
}
function toast(message, kind = '', link = null) {
  const el = $('#toast');
  el.className = `toast ${kind}`;
  el.innerHTML = esc(message) + (link ? ` <a href="${esc(link[0])}">${esc(link[1])}</a>` : '');
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.hidden = true; }, 6000);
}

async function api(path, options = {}) {
  const headers = { 'X-ASCAM-UI': '1' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, {
    method: options.method || 'GET', headers: headers, credentials: 'same-origin',
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  if (res.status === 401 && !path.startsWith('/api/ui/login')) {
    showLogin();
    throw new Error('Session expired. Please log in again.');
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === 'string' ? detail : `Request failed (HTTP ${res.status})`);
  }
  return data;
}
const k = (path, options) => api(`/api/k/${path}`, options);

/* ── events and actions explained in plain sentences ─────────────────────── */
function eventObject(s) {
  const location = [s.schema, s.table].filter(Boolean).join('.');
  return `${s.source || '?'}: ${location}`;
}
function eventSentence(s) {
  if (!s) return 'Schema change';
  const location = eventObject(s);
  switch (s.operation) {
    case 'add': return `New column ${s.column}${s.column_type ? ` (${s.column_type})` : ''} in ${location}`;
    case 'drop': return `Column ${s.column} dropped from ${location}`;
    case 'rename': return `Column ${s.column} renamed to ${s.new_column} in ${location}`;
    default: return `Unsupported change in ${location}`;
  }
}
function rangeFor(type) {
  if (!type) return null;
  const base = String(type).toLowerCase().split('(')[0].trim();
  return S.types[String(type).toLowerCase()] || S.types[base] || null;
}
function explain(a, plan) {
  const p = a.params || {};
  const columnType = (plan.actions.find((x) => x.params && x.params.column_type) || {}).params?.column_type;
  switch (`${a.artifact}:${a.operation}`) {
    case 'vdb:add_column':
      return [`Add column ${p.column} to table ${p.table}`, `VDB model ${p.model}, source type ${p.column_type || '-'}; written as ALTER FOREIGN TABLE in a new VDB version`];
    case 'vdb:drop_column':
      return [`Drop column ${p.column} from table ${p.table}`, `VDB model ${p.model}; the old VDB version remains available for rollback`];
    case 'vdb:alter_view':
      return [`Remove ${(p.removed_columns || []).join(', ')} from view ${p.model}.${p.table}`, `New definition: ${p.body}; written as ALTER VIEW in a new VDB version`];
    case 'vdb:set_name_in_source':
      return [`Point ${p.table}.${p.column} to the new source name ${p.name_in_source}`, 'The Teiid column name does not change, so the mapping and the ontology stay intact (alias strategy)'];
    case 'ontology:add_datatype_property': {
      const range = rangeFor(columnType);
      return [`Add property ${short(p.iri)}`, `IRI ${p.iri}; domain ${(p.domain || []).map(short).join(', ') || '-'}; range ${range || 'from the type mapping'}`];
    }
    case 'ontology:deprecate_property':
      return [`Mark ${short(p.predicate_iri)} as deprecated`, 'Its declaration stays, with owl:deprecated, so that existing queries do not fail'];
    case 'ontology:keep_property':
      return [`Keep ${short(p.predicate_iri)}`, 'This property is still used by another mapping'];
    case 'r2rml:add_predicate_object_map':
      return [`Map column ${p.column} to property ${short(p.predicate_iri)}`, `In TriplesMap ${short(p.triples_map_iri) || p.table}; added as a managed block without rewriting the mapping`];
    case 'r2rml:remove_predicate_object_map':
      return [`Remove the mapping of ${short(p.predicate_iri)}`, `From ${short(p.triples_map_iri) || 'every TriplesMap'}`];
    case 'r2rml:rewrite_logical_table':
      return [`Remove column ${p.column} from the logical-table query`, `In ${short(p.triples_map_iri)}`];
    default:
      return [`${a.operation}`, JSON.stringify(p)];
  }
}
function actionList(plan) {
  if (!plan.actions || !plan.actions.length) {
    return '<p class="muted">No automatic actions. The administrator must handle this change by hand.</p>';
  }
  return `<ul class="action-list">${plan.actions.map((a) => {
    const [title, detail] = explain(a, plan);
    const l = LAYERS[a.artifact] || { name: a.artifact, cls: '' };
    return `<li class="${esc(a.artifact)}"><span class="badge ${l.cls}">${esc(l.name)}</span>
      <div><strong>${esc(title)}</strong><div class="detail">${esc(detail)}</div></div></li>`;
  }).join('')}</ul>`;
}
function reasonList(reasons) {
  if (!reasons || !reasons.length) return '<p class="muted">No notes.</p>';
  return `<ul class="reasons">${reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>`;
}
function usageTable(impact) {
  const rows = [];
  for (const t of (impact && impact.targets) || []) {
    for (const u of t.usages || []) {
      rows.push(`<tr><td>${esc(t.table)}.${esc(t.column)}</td><td>${esc(short(u.triples_map))}</td>
        <td>${esc(u.role)}</td><td>${esc(short(u.predicate_iri))}</td><td>${esc(u.weakest_link)}</td></tr>`);
    }
  }
  if (!rows.length) return '<p class="muted">No mapping uses this column yet.</p>';
  return `<div class="scroll"><table><thead><tr><th>Teiid column</th><th>TriplesMap</th><th>Role</th>
    <th>Predicate</th><th>Weakest edge</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}

/* ── shared data loading ───────────────────────────────────────────────────── */
async function loadTypes() {
  if (Object.keys(S.types).length) return;
  try {
    for (const t of await k(`obdf/${S.oid}/type-mappings`)) S.types[t.native_type.toLowerCase()] = t.xsd_datatype;
  } catch { /* optional */ }
}
async function loadEvents() {
  for (const e of await k(`obdf/${S.oid}/events?limit=200`)) S.events[e.id] = e;
}

/* ── page: overview ────────────────────────────────────────────────────────── */
function stepBar(execution) {
  const steps = (execution.steps || []).filter((s) => s.detail && s.detail.duration_ms);
  const total = steps.reduce((n, s) => n + s.detail.duration_ms, 0) || 1;
  const pieces = steps.map((s) => `<span class="l-${esc(s.name)}" style="width:${(100 * s.detail.duration_ms / total).toFixed(2)}%"
      title="${esc(STEP_NAMES[s.name] || s.name)}: ${esc(duration(s.detail.duration_ms))}"></span>`).join('');
  return `<div class="bar" role="img" aria-label="Duration of the execution steps">${pieces}</div>`;
}
function stepLegend() {
  return `<div class="legend">${STEPS.map((n) => `<span><i class="l-${n}"></i>${esc(STEP_NAMES[n])}</span>`).join('')}</div>`;
}
function executionRow(e) {
  return `<tr><td>${esc(e.plan_id)}</td><td>${badge(e.status)}</td><td>${esc(time(e.started_at))}</td>
    <td>${esc(duration(e.timings && e.timings.total_ms))}</td><td>${stepBar(e)}</td></tr>`;
}

function currentRoute() {
  return (location.hash.replace(/^#\//, '') || 'overview').split('?')[0];
}
async function overviewPage() {
  const [r, health] = await Promise.all([api('/api/ui/overview'), api('/api/ui/health')]);
  // a periodic refresh that finishes after the user moved to another page must not overwrite it
  if (ROUTES[currentRoute()] && currentRoute() !== 'overview') return;
  const ev = r.events[0];
  const ex = r.executions[0];
  const exe = health.executor || {};
  const version = r.active_version || {};
  const notices = r.notifications.filter((n) => !r.pending.some((p) => p.id === n.plan_id)).length;
  const stages = [
    { name: 'Monitor', value: r.events.length ? relative(ev.received_at) : '-',
      note: ev ? eventSentence(ev.structured) : 'No schema change yet', link: '#/changes' },
    { name: 'Analyze', value: ev ? (STATUS[ev.status] || [ev.status])[0] : '-',
      note: ev && ev.ignore_reason ? ev.ignore_reason : 'The impact is computed from the column lineage', link: '#/plans' },
    { name: 'Plan', value: `${r.pending.length} waiting`, attention: r.pending.length > 0 || notices > 0,
      note: [r.pending.length ? 'A column addition is waiting for your approval' : 'No proposal is waiting',
        notices ? `${notices} notification(s) of changes that were not adapted` : ''].filter(Boolean).join('; '),
      link: r.pending.length || !notices ? '#/approvals' : '#/notifications' },
    { name: 'Execute', value: ex ? (STATUS[ex.status] || [ex.status])[0] : '-', troubled: ex && ['failed', 'rolled_back'].includes(ex.status),
      note: ex ? `Plan ${ex.plan_id}, ${duration(ex.timings && ex.timings.total_ms)}${exe.paused ? '; executor paused' : ''}` : 'No execution yet', link: '#/plans' },
    { name: 'Knowledge', value: version.version_no ? `Version ${version.version_no}` : '-',
      note: version.teiid_vdb_name ? `VDB ${version.teiid_vdb_name} v${version.teiid_vdb_version}, ${version.teiid_connection_type || ''}` : '', link: '#/versions' },
  ];
  const components = [
    ['Knowledge', health.knowledge, (d) => d.status || (d._error ? d._error : 'ready')],
    ['Orchestrator', health.orchestrator, (d) => d.state ? `${d.state}; ${d.messages ?? 0} message(s), ${d.events_sent ?? 0} event(s), ${d.skipped ?? 0} skipped, ${d.failures ?? 0} failed` : d._error],
    ['Executor', health.executor, (d) => d.state ? `${d.paused ? 'paused' : d.state}; ${d.executed ?? 0} applied, ${d.rolled_back ?? 0} rolled back, ${d.failed ?? 0} failed` : d._error],
    ['Ontop Agent', health.agent, (d) => d.status ? `${d.status}; artifacts ${Object.entries(d.artifacts || {}).map(([a, b]) => `${a} ${b ? 'present' : 'missing'}`).join(', ')}` : d._error],
  ];
  content().innerHTML = `
    <div class="page-header"><div><h1>Overview</h1>
      <p class="muted">State of the adaptation loop of ${esc(r.obdf.name)} and of the ASCAM components. Refreshed every five seconds.</p></div></div>
    <nav class="cycle" aria-label="Adaptation loop">${stages.map((t) => `
      <a class="stage ${t.attention ? 'attention' : ''} ${t.troubled ? 'troubled' : ''}" href="${t.link}">
        <span class="name">${esc(t.name)}</span><span class="value">${esc(t.value)}</span>
        <span class="note">${esc(t.note)}</span></a>`).join('')}</nav>
    <div class="grid-two" style="margin-top:1.25rem">
      <div class="panel"><h3>ASCAM components</h3><table><tbody>${components.map(([name, d, f]) => {
        const ok = d && !d._error && !['failed', 'degraded'].includes(d.state) && d.status !== 'degraded';
        return `<tr><td><strong>${esc(name)}</strong></td><td>${ok ? '<span class="badge success">Healthy</span>' : '<span class="badge failure">Problem</span>'}</td>
          <td class="small muted">${esc(f(d || {}) || '-')}</td></tr>`;
      }).join('')}</tbody></table>
      <p style="margin-top:1rem"><button class="small" id="check-targets" type="button">Check the connections to Teiid, Ontop, and Kafka</button></p>
      <div id="check-results"></div></div>
      <div class="panel"><h3>Latest schema changes</h3>${r.events.length ? `<table><tbody>${r.events.map((e) => `
        <tr><td class="small muted time">${esc(relative(e.received_at))}</td><td>${esc(eventSentence(e.structured))}</td><td>${badge(e.status)}</td></tr>`).join('')}
        </tbody></table>` : '<p class="empty">No schema change detected yet.</p>'}</div>
    </div>
    <div class="panel"><h3>Latest executions</h3>${r.executions.length ? `<div class="scroll"><table>
      <thead><tr><th>Plan</th><th>Status</th><th>Started</th><th>Total</th><th>Duration per step</th></tr></thead>
      <tbody>${r.executions.map(executionRow).join('')}</tbody></table></div>${stepLegend()}` : '<p class="empty">No plan has been executed yet.</p>'}</div>`;
  $('#check-targets').addEventListener('click', checkTargets);
}

async function checkTargets() {
  const button = $('#check-targets');
  button.disabled = true;
  button.textContent = 'Checking connections';
  try {
    const result = await k(`obdf/${S.oid}/checks`, { method: 'POST', body: {} });
    $('#check-results').innerHTML = `<table style="margin-top:0.75rem"><tbody>${result.map((h) => `
      <tr><td>${h.ok ? '<span class="badge success">Connected</span>' : '<span class="badge failure">Failed</span>'}</td>
      <td class="small">${esc(h.detail && h.detail.summary)}</td><td class="small muted">${esc(h.latency_ms)} ms</td></tr>`).join('')}</tbody></table>`;
  } catch (e) { toast(e.message, 'failure'); }
  button.disabled = false;
  button.textContent = 'Check the connections to Teiid, Ontop, and Kafka';
}

/* ── page: approvals (column additions only, ADR-0024) ───────────────────── */
function camel(column) {
  return String(column || '').split('_').map((b, i) => (i ? b.charAt(0).toUpperCase() + b.slice(1).toLowerCase() : b)).join('');
}
function lineageLink(plan) {
  const s = (S.events[plan.event_id] || {}).structured;
  if (!s || !s.table) return '';
  const q = new URLSearchParams({ source: s.source || '', table: s.table, column: s.column || '' });
  return `<p style="margin-top:0.75rem"><a href="#/lineage?${esc(q.toString())}">See its paths in the column lineage</a></p>`;
}
function affectedList(plan, { link = true } = {}) {
  const a = plan.impact && plan.impact.affected;
  if (!a) return '';
  const groups = [['columns', 'Columns (Σ_S)', 'sigma'], ['views', 'Views (Σ_V)', 'sigma'],
    ['triples_maps', 'TriplesMaps (ℳ)', 'mapping'], ['predicates', 'Properties (𝒯)', 'ontology']]
    .filter(([key]) => a[key] && a[key].length);
  if (!groups.length) return '';
  return `<section><h3>Affected OBDF elements</h3><ul class="affected">${groups.map(([key, title, cls]) => `
    <li><span class="badge ${cls}">${esc(title)}</span><span>${a[key].map((x) => esc(['columns', 'views'].includes(key) ? x : short(x))).join(', ')}</span></li>`).join('')}</ul>
    ${link ? lineageLink(plan) : ''}</section>`;
}
function propertyForm(plan) {
  const o = plan.impact && plan.impact.add_options;
  if (!o) return '';
  if (!o.triples_maps || !o.triples_maps.length || !o.namespace || !o.iri_template) {
    return `<section><h3>New ontology property</h3><p class="muted">No property can be added:
      ${!o.triples_maps || !o.triples_maps.length ? 'no TriplesMap exposes the new column by itself, so approval only adds the column to the VDB.' : 'the naming policy or the ontology namespace is not set.'}</p></section>`;
  }
  const id = esc(plan.id);
  const domain = o.proposed_domain || (o.classes || [])[0] || '';
  return `<section><h3>New ontology property</h3>
    <p class="muted small" style="margin-bottom:0.75rem">${o.proposed_iri ? 'Proposal of ASCAM from the naming policy. Change it if needed; approving with another name or domain is recorded as a revised plan.'
      : 'ASCAM cannot propose a name free of collisions. Choose the property name before approving.'}</p>
    <div class="form-row">
      <div><label for="prop-${id}">Property name</label>
        <input id="prop-${id}" data-property="${id}" value="${esc(o.proposed_iri ? short(o.proposed_iri) : '')}" placeholder="${esc(camel(o.column))}" maxlength="100" autocomplete="off" spellcheck="false"></div>
      <div><label for="dom-${id}">Domain (subject class)</label>${(o.classes || []).length
        ? `<select id="dom-${id}" data-property="${id}">${o.classes.map((c) => `<option value="${esc(c)}" ${c === domain ? 'selected' : ''}>${esc(short(c))}</option>`).join('')}</select>`
        : '<p class="small muted" style="padding-top:0.5rem">The exposing TriplesMaps give no class, so the property has no domain.</p>'}</div>
    </div>
    <p class="small property-preview" id="preview-${id}"></p></section>`;
}
function propertyChoice(id) {
  const plan = S.plans[id];
  const o = plan && plan.impact && plan.impact.add_options;
  const input = $(`#prop-${id}`);
  if (!o || !input) return null;
  const select = $(`#dom-${id}`);
  return { o, name: input.value.trim(), domain: select ? select.value : null };
}
function propertyPreview(id) {
  const p = propertyChoice(id);
  if (!p) return;
  const el = $(`#preview-${id}`);
  if (!/^[A-Za-z_][A-Za-z0-9_-]{0,99}$/.test(p.name)) {
    el.innerHTML = '<span style="color:var(--failure)">A name starts with a letter or underscore, followed by letters, digits, underscores, or hyphens.</span>';
    return;
  }
  const iri = p.o.iri_template.replace('{namespace}', p.o.namespace).replace('{column_camel}', p.name).replace('{column}', p.name);
  const tm = p.o.triples_maps.filter((t) => !p.domain || !t.classes.length || t.classes.includes(p.domain)).map((t) => short(t.iri));
  el.innerHTML = `IRI <code>${esc(iri)}</code>; column ${esc(p.o.column)} is mapped in ${tm.length ? tm.map(esc).join(', ') : '<strong>no TriplesMap</strong>'}.`;
}

function proposalDocument(plan, { decidable, preview = false }) {
  const ev = S.events[plan.event_id];
  const structured = ev ? ev.structured : null;
  const notification = plan.decision === 'notify' || plan.status === 'notify';
  const actionsTitle = notification ? 'ASCAM actions' : ['executed', 'failed'].includes(plan.status) ? 'What ASCAM did' : 'What ASCAM will do';
  const actions = notification && !(plan.actions || []).length
    ? '<p class="muted">ASCAM does not change the OBDF for this change. Repair the specification by hand at the affected elements, then run a sync so that Knowledge loads the new version.</p>'
    : actionList(plan);
  const reasonsTitle = decidable ? 'Why your decision is needed' : notification ? 'Why it was not adapted automatically' : 'Analysis notes';
  return `<article class="proposal ${notification ? 'notice' : ''}" id="proposal-${esc(plan.id)}">
    <header><h2>${esc(eventSentence(structured))}</h2>${badge(plan.status)}
      <span class="small muted">${preview ? `Pattern ${esc(plan.pattern || '-')}, computed on specification version ${esc(plan.base_spec_version_id)}`
        : `Plan ${esc(plan.id)}${plan.revision_of ? ` (revision of plan ${esc(plan.revision_of)})` : ''}, pattern ${esc(plan.pattern || '-')}, built ${esc(relative(plan.created_at))} on specification version ${esc(plan.base_spec_version_id)}`}</span></header>
    <section><h3>${actionsTitle}</h3>${actions}</section>
    ${decidable ? propertyForm(plan) : ''}
    ${plan.reasons && plan.reasons.length ? `<section><h3>${reasonsTitle}</h3>${reasonList(plan.reasons)}</section>` : ''}
    ${affectedList(plan)}
    ${plan.impact && plan.impact.targets && plan.impact.targets.length ? `<section><h3>Current use of the column</h3>${usageTable(plan.impact)}</section>` : ''}
    ${decidable ? `<section class="admin-decision">
      <div><label for="note-${esc(plan.id)}">Decision note (kept in the audit trail)</label>
        <textarea id="note-${esc(plan.id)}" placeholder="For example: the property name follows the naming rules"></textarea></div>
      <div class="button-group">
        <button class="reject" type="button" data-decide="reject" data-plan="${esc(plan.id)}">Reject</button>
        <button class="approve" type="button" data-decide="approve" data-plan="${esc(plan.id)}">Approve and apply</button>
      </div></section>` : preview ? '<p class="small muted" style="margin-top:1rem">Preview. Nothing in the OBDF is changed.</p>'
      : plan.decided_by ? `<p class="small muted" style="margin-top:1rem">Decided by ${esc(plan.decided_by)} ${esc(relative(plan.decided_at))}</p>` : ''}
  </article>`;
}

async function approvalsPage() {
  await Promise.all([loadEvents(), loadTypes()]);
  const [pending, all] = await Promise.all([k(`obdf/${S.oid}/plans?status=pending_approval`),
    k(`obdf/${S.oid}/plans?limit=40`)]);
  for (const p of pending) S.plans[p.id] = p;
  const decided = all.filter((p) => p.decided_by && p.decision === 'hitl' && p.status !== 'revised').slice(0, 8);
  content().innerHTML = `
    <div class="page-header"><div><h1>Approvals</h1>
      <p class="muted">A column addition adds a new property to the ontology, so it always waits for your decision. Check the name and domain of the property and the actions on every OBDF layer, then approve or reject. Changes that cannot be adapted automatically do not appear here but on the Notifications page.</p></div></div>
    ${pending.length ? pending.map((p) => proposalDocument(p, { decidable: true })).join('')
      : '<div class="panel"><p class="empty">No proposal is waiting for a decision. Column additions to federated tables will appear here.</p></div>'}
    ${decided.length ? `<div class="panel" style="margin-top:1.25rem"><h3>Latest decisions</h3><table>
      <thead><tr><th>Plan</th><th>Change</th><th>Decision</th><th>By</th><th>Time</th></tr></thead><tbody>
      ${decided.map((p) => `<tr><td>${esc(p.id)}${p.revision_of ? ` <span class="small muted">revision of ${esc(p.revision_of)}</span>` : ''}</td><td>${esc(eventSentence((S.events[p.event_id] || {}).structured))}</td>
        <td>${badge(p.status)}</td><td>${esc(p.decided_by)}</td><td>${esc(time(p.decided_at))}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
  content().querySelectorAll('[data-decide]').forEach((b) => b.addEventListener('click', decide));
  content().querySelectorAll('[data-property]').forEach((el) => el.addEventListener('input', () => propertyPreview(el.dataset.property)));
  pending.forEach((p) => propertyPreview(String(p.id)));
}

async function decide(ev) {
  const button = ev.currentTarget;
  const id = button.dataset.plan;
  const action = button.dataset.decide;
  const note = $(`#note-${id}`).value.trim();
  if (action === 'reject' && !note) {
    toast('Write the reason for the rejection in the decision note.', 'failure');
    $(`#note-${id}`).focus();
    return;
  }
  const body = { note: note || null };
  const choice = action === 'approve' ? propertyChoice(id) : null;
  if (choice) {
    if (!choice.name) {
      toast('Choose the property name before approving.', 'failure');
      $(`#prop-${id}`).focus();
      return;
    }
    body.property_name = choice.name;
    body.domain = choice.domain;
  }
  content().querySelectorAll(`[data-plan="${id}"]`).forEach((b) => { b.disabled = true; });
  try {
    const result = await k(`plans/${id}/${action}`, { method: 'POST', body });
    toast(action === 'reject' ? 'Rejected. The plan will not be applied.'
      : result.revision_of ? `Approved with a revision as plan ${result.id}. The Executor will apply it in a few seconds.`
        : 'Approved. The Executor will apply this plan in a few seconds.',
    action === 'approve' ? 'decision' : '', action === 'approve' ? ['#/plans', 'Follow the execution'] : null);
    await refreshBadges();
    await approvalsPage();
  } catch (e) {
    toast(e.message, 'failure');
    content().querySelectorAll(`[data-plan="${id}"]`).forEach((b) => { b.disabled = false; });
  }
}

/* ── page: notifications (unsafe DROP/RENAME, ADR-0024) ──────────────────── */
async function notificationsPage() {
  const all = (location.hash.split('?')[1] || '').includes('all=1');
  await loadEvents();
  const [listing, plans] = await Promise.all([
    k(`obdf/${S.oid}/notifications?limit=100${all ? '' : '&unread=true'}`), k(`obdf/${S.oid}/plans?limit=200`)]);
  for (const p of plans) S.plans[p.id] = p;
  content().innerHTML = `
    <div class="page-header"><div><h1>Notifications</h1>
      <p class="muted">Schema changes that cannot be shown to be safe are not adapted and need no approval. ASCAM reports the affected OBDF elements; repair the specification by hand, then run a sync.</p></div>
      <div class="actions"><a class="btn" href="#/notifications${all ? '' : '?all=1'}">${all ? 'Unread only' : 'Show all'}</a>
        <button class="primary" id="sync-button" type="button">Run a sync</button></div></div>
    ${listing.length ? listing.map((n) => notificationCard(n, S.plans[n.plan_id])).join('')
      : `<div class="panel"><p class="empty">${all ? 'No notifications yet.' : 'No unread notifications.'}</p></div>`}`;
  content().querySelectorAll('[data-read]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await k(`notifications/${b.dataset.read}/ack`, { method: 'POST', body: {} });
      await refreshBadges();
      await notificationsPage();
    } catch (e) { toast(e.message, 'failure'); b.disabled = false; }
  }));
  $('#sync-button').addEventListener('click', async (ev) => {
    const button = ev.currentTarget;
    button.disabled = true;
    try {
      const h = await k(`obdf/${S.oid}/sync`, { method: 'POST', body: {} });
      toast(h.changed ? `Sync finished: specification version ${h.version_no} is now active.` : 'Sync finished: the OBDF specification did not change.');
      await refreshBadges();
    } catch (e) { toast(e.message, 'failure'); }
    button.disabled = false;
  });
}

function notificationCard(n, plan) {
  const s = plan ? (S.events[plan.event_id] || {}).structured : null;
  const followUp = !plan ? '' : plan.status === 'pending_approval'
    ? '<a class="btn" href="#/approvals">Review the proposal</a>'
    : plan.decision === 'notify' && s && s.table
      ? `<a class="btn" href="#/lineage?${esc(new URLSearchParams({ source: s.source || '', table: s.table, column: s.column || '' }).toString())}">See the lineage</a>` : '';
  const body = plan && plan.decision === 'notify'
    ? `${affectedList(plan, { link: false })}${plan.reasons && plan.reasons.length ? `<section><h3>Why it was not adapted automatically</h3>${reasonList(plan.reasons)}</section>` : ''}`
    : `<p style="margin-top:0.75rem">${esc(n.message)}</p>`;
  return `<article class="proposal ${plan && plan.decision === 'notify' ? 'notice' : ''}">
    <header><h2>${esc(s ? eventSentence(s) : 'Notification')}</h2>${plan ? badge(plan.status) : badge(n.severity)}
      <span class="small muted">${esc(relative(n.created_at))}${plan ? `, plan ${esc(plan.id)}` : ''}${n.acknowledged ? `; read by ${esc(n.acknowledged_by)}` : ''}</span></header>
    ${body}
    <div class="button-group" style="margin-top:1rem">${followUp}${n.acknowledged ? '' : `<button type="button" data-read="${esc(n.id)}">Mark as read</button>`}</div>
  </article>`;
}

/* ── page: column lineage of the active specification ──────────────────────── */
function nodeLayer(n) {
  if (n.layer === 'sigma') return n.kind === 'view_column' ? 'view' : 'foreign';
  return n.layer;
}
async function lineagePage() {
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  const data = await k(`obdf/${S.oid}/lineage`);
  const nodes = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
  const tables = [...new Set(data.paths.map((p) => { const n = nodes[p.nodes[0]]; return `${n.source}|${n.table}`; }))].sort();
  const chosenTable = q.get('source') && q.get('table') ? `${q.get('source')}|${q.get('table')}` : tables[0];
  content().innerHTML = `
    <div class="page-header"><div><h1>Column lineage</h1>
      <p class="muted">The path of every source column to an ontology property in the active specification version ${esc(data.version_no)}. Select a node to highlight all paths through it; those paths are what is affected when its column is dropped or renamed.</p></div></div>
    <div class="panel"><div class="form-row" style="margin-bottom:0">
      <div><label for="l-table">Source table</label><select id="l-table">
        ${tables.map((t) => { const [a, b] = t.split('|'); return `<option value="${esc(t)}" ${t === chosenTable ? 'selected' : ''}>${esc(a)}: ${esc(b)}</option>`; }).join('')}
        <option value="*" ${chosenTable === '*' ? 'selected' : ''}>All tables</option></select></div>
      <div><label for="l-column">Highlight source column</label><select id="l-column"></select></div>
    </div></div>
    <div class="panel lineage-panel"><div class="lineage" id="lineage"></div>${lineageLegend()}</div>
    <div class="panel" id="lineage-detail"></div>`;
  const draw = () => {
    const t = $('#l-table').value;
    const paths = data.paths.filter((p) => { const n = nodes[p.nodes[0]]; return t === '*' || `${n.source}|${n.table}` === t; });
    const columns = [...new Set(paths.map((p) => nodes[p.nodes[0]].column))].sort();
    const highlighted = q.get('column') && columns.includes(q.get('column')) ? q.get('column') : '';
    q.delete('column');
    $('#l-column').innerHTML = `<option value="">None</option>${columns.map((c) => `<option value="${esc(c)}" ${c === highlighted ? 'selected' : ''}>${esc(c)}</option>`).join('')}`;
    drawLineage(paths, nodes, data.edges);
    const start = paths.find((p) => nodes[p.nodes[0]].column === highlighted);
    highlightLineage(start ? start.nodes[0] : null);
  };
  $('#l-table').addEventListener('change', draw);
  $('#l-column').addEventListener('change', () => {
    const c = $('#l-column').value;
    const start = S.lineage.paths.find((p) => S.lineage.nodes[p.nodes[0]].column === c);
    highlightLineage(c && start ? start.nodes[0] : null);
  });
  draw();
}

function lineageLegend() {
  return `<div class="legend">
    <span><i class="edge-l"></i>Pass-through or direct edge</span>
    <span><i class="edge-l weak"></i>Expression or SQL predicate (not safe for automatic adaptation)</span>
    <span><i class="box-l unused"></i>Column not mapped to the ontology</span>
    <span><i class="box-l deprecated"></i>Deprecated property (owl:deprecated)</span></div>`;
}

const WEAK = new Set(['expression', 'predicate', 'iri_template', 'join_key', 'sql_predicate', 'dynamic_predicate']);
function drawLineage(paths, nodes, edges) {
  const container = $('#lineage');
  const used = new Set(paths.flatMap((p) => p.nodes));
  const order = new Map();
  paths.forEach((p) => p.nodes.forEach((id) => { if (!order.has(id)) order.set(id, order.size); }));
  const unusedColumns = new Set(paths.filter((p) => p.role === null).map((p) => p.nodes[1]));
  container.innerHTML = `<svg class="lineage-edges" aria-hidden="true"></svg>${LINEAGE_LAYERS.map((l) => {
    const layerNodes = [...used].filter((id) => nodeLayer(nodes[id]) === l.key).sort((a, b) => order.get(a) - order.get(b));
    return `<div class="lineage-column"><h3 class="small">${esc(l.title)}</h3>${layerNodes.map((id) => {
      const n = nodes[id];
      const label = n.kind === 'source_column' ? n.column : n.kind.endsWith('_column') ? `${n.table.split('.').pop()}.${n.column}` : n.label;
      const cls = [l.cls, unusedColumns.has(id) ? 'unused' : '', n.deprecated ? 'deprecated' : ''].join(' ');
      return `<button type="button" class="node ${cls}" data-node="${esc(id)}" title="${esc(n.iri || n.label)}">${esc(label)}</button>`;
    }).join('') || '<p class="small muted">-</p>'}</div>`;
  }).join('')}`;
  S.lineage = { paths, nodes, edges: edges.filter((e) => used.has(e.from) && used.has(e.to)), selected: null };
  container.querySelectorAll('[data-node]').forEach((b) => b.addEventListener('click', () => {
    highlightLineage(S.lineage.selected === b.dataset.node ? null : b.dataset.node);
  }));
  lineageEdges();
}

function lineageEdges() {
  const container = $('#lineage');
  if (!container || !S.lineage) return;
  const svg = container.querySelector('svg');
  const box = container.getBoundingClientRect();
  svg.setAttribute('width', container.scrollWidth);
  svg.setAttribute('height', container.scrollHeight);
  const point = (id, side) => {
    const el = container.querySelector(`[data-node="${CSS.escape(id)}"]`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [(side === 'right' ? r.right : r.left) - box.left + container.scrollLeft, r.top + r.height / 2 - box.top + container.scrollTop];
  };
  const lit = S.lineage.lit || null;
  svg.innerHTML = S.lineage.edges.map((e) => {
    const a = point(e.from, 'right');
    const b = point(e.to, 'left');
    if (!a || !b) return '';
    const dx = Math.max(24, (b[0] - a[0]) / 2);
    const cls = [WEAK.has(e.kind) ? 'weak' : '', lit ? (lit.has(`${e.from}>${e.to}`) ? 'highlight' : 'faded') : ''].join(' ');
    return `<path class="${cls}" d="M${a[0]},${a[1]} C${a[0] + dx},${a[1]} ${b[0] - dx},${b[1]} ${b[0]},${b[1]}"><title>${esc(ROLES[e.kind] || e.kind)}</title></path>`;
  }).join('');
}

function highlightLineage(id) {
  const L = S.lineage;
  L.selected = id;
  const hit = id ? L.paths.filter((p) => p.nodes.includes(id)) : [];
  L.lit = id ? new Set(hit.flatMap((p) => p.nodes.slice(1).map((n, i) => `${p.nodes[i]}>${n}`))) : null;
  const litNodes = new Set(hit.flatMap((p) => p.nodes));
  $('#lineage').querySelectorAll('[data-node]').forEach((b) => {
    b.classList.toggle('highlight', litNodes.has(b.dataset.node));
    b.classList.toggle('faded', Boolean(id) && !litNodes.has(b.dataset.node));
    b.setAttribute('aria-pressed', b.dataset.node === id ? 'true' : 'false');
  });
  lineageEdges();
  const detail = $('#lineage-detail');
  if (!id) {
    detail.innerHTML = '<p class="muted">Select a node in the lineage to see the paths through it.</p>';
    return;
  }
  detail.innerHTML = `<h3>${esc(hit.length)} path(s) pass through ${esc(L.nodes[id].label)}</h3><div class="scroll"><table>
    <thead><tr><th>Path</th><th>Role in the mapping</th><th>Weakest edge</th></tr></thead><tbody>
    ${hit.map((p) => `<tr><td>${p.nodes.map((n) => esc(L.nodes[n].label)).join(' <span class="muted">⊳</span> ')}</td>
      <td>${p.role ? esc(ROLES[p.role] || p.role) : '<span class="muted">not mapped</span>'}</td>
      <td>${p.weakest_link ? `<span class="${WEAK.has(p.weakest_link) ? 'badge warning' : ''}">${esc(ROLES[p.weakest_link] || p.weakest_link)}</span>` : '-'}</td></tr>`).join('')}
    </tbody></table></div>`;
}

/* ── page: schema changes ──────────────────────────────────────────────────── */
async function changesPage() {
  const listing = await k(`obdf/${S.oid}/events?limit=100`);
  content().innerHTML = `
    <div class="page-header"><div><h1>Schema changes</h1>
      <p class="muted">Every DDL statement captured by the monitor at the sources, with the result of its analysis. Changes to tables that are not federated are recorded as ignored.</p></div></div>
    <div class="panel scroll">${listing.length ? `<table><thead><tr><th>Received</th><th>Change</th><th>Status</th><th>Note</th></tr></thead><tbody>
      ${listing.map((e) => `<tr><td class="small">${esc(time(e.received_at))}</td><td>${esc(eventSentence(e.structured))}</td>
        <td>${badge(e.status)}</td><td class="small muted">${esc(e.ignore_reason || '')}</td></tr>`).join('')}</tbody></table>`
      : '<p class="empty">No schema change yet. The monitor records every ALTER TABLE at the registered sources.</p>'}</div>`;
}

/* ── page: plans and executions ────────────────────────────────────────────── */
async function plansPage() {
  await Promise.all([loadEvents(), loadTypes()]);
  const [plans, executions] = await Promise.all([k(`obdf/${S.oid}/plans?limit=60`), k(`obdf/${S.oid}/executions?limit=60`)]);
  const perPlan = {};
  for (const e of executions) (perPlan[e.plan_id] = perPlan[e.plan_id] || []).push(e);
  content().innerHTML = `
    <div class="page-header"><div><h1>Plans and executions</h1>
      <p class="muted">The adaptation plans built for every change, and the trail of how they were applied. Select a row to see its details.</p></div></div>
    <div class="panel scroll">${plans.length ? `<table><thead><tr><th>Plan</th><th>Change</th><th>Pattern</th><th>Decision</th><th>Status</th><th>Execution</th></tr></thead><tbody>
      ${plans.map((p) => { const e = (perPlan[p.id] || [])[0]; return `<tr class="selectable" data-plan-row="${esc(p.id)}" tabindex="0">
        <td>${esc(p.id)}</td><td>${esc(eventSentence((S.events[p.event_id] || {}).structured))}</td><td class="time">${esc(p.pattern || '-')}</td>
        <td>${badge(p.decision)}</td><td>${badge(p.status)}</td><td>${e ? `${badge(e.status)} <span class="small muted">${esc(duration(e.timings && e.timings.total_ms))}</span>` : '-'}</td></tr>`; }).join('')}
      </tbody></table>` : '<p class="empty">No adaptation plan yet.</p>'}</div>
    <div id="plan-detail"></div>`;
  content().querySelectorAll('[data-plan-row]').forEach((tr) => {
    const open = () => planDetail(plans.find((p) => String(p.id) === tr.dataset.planRow), perPlan[tr.dataset.planRow] || []);
    tr.addEventListener('click', open);
    tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(); });
  });
}

function planDetail(plan, executionList) {
  const container = $('#plan-detail');
  container.innerHTML = `<div style="margin-top:1.25rem">${proposalDocument(plan, { decidable: false })}</div>
    ${executionList.map((e) => `<div class="panel"><h3>Execution ${esc(e.id)}: ${(STATUS[e.status] || [e.status])[0]}</h3>
      <ul class="timeline">${e.steps.map((s) => `<li><span>${esc(STEP_NAMES[s.name] || s.name)}</span><span>${badge(s.status)}</span>
        <span class="small muted">${esc(duration(s.detail && s.detail.duration_ms))}${s.detail && s.detail.error ? `; ${esc(s.detail.error)}` : ''}${s.detail && s.detail.statements ? `; ${esc(s.detail.statements.join(' '))}` : ''}</span></li>`).join('')}</ul>
      ${e.failure ? `<p class="small" style="margin-top:0.75rem;color:var(--failure)">${esc(e.failure.message || JSON.stringify(e.failure))}</p>` : ''}
      ${e.timings && e.timings.total_ms ? `<p class="small muted" style="margin-top:0.5rem">Total ${esc(duration(e.timings.total_ms))}</p>` : ''}</div>`).join('')}`;
  container.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
}

/* ── page: specification versions and artifact comparison ─────────────────── */
async function versionsPage() {
  const versions = await k(`obdf/${S.oid}/versions?limit=60`);
  const options = versions.map((v) => `<option value="${esc(v.id)}">Version ${esc(v.version_no)} (${esc((STATUS[v.status] || [v.status])[0])})</option>`).join('');
  content().innerHTML = `
    <div class="page-header"><div><h1>Specification versions</h1>
      <p class="muted">Every version is a complete snapshot of the OBDF: VDB schema, mapping, and ontology. Compare two versions to see what an adaptation changed.</p></div></div>
    <div class="panel"><h3>Compare artifacts</h3>
      <div class="form-row">
        <div><label for="from">From</label><select id="from">${options}</select></div>
        <div><label for="to">To</label><select id="to">${options}</select></div>
        <div><label for="kind">Artifact</label><select id="kind">
          <option value="r2rml">Mapping (ℳ)</option><option value="ontology">Ontology (𝒯)</option><option value="vdb_xml">VDB (Σ_S)</option></select></div>
      </div>
      <button class="primary" id="compare-button" type="button">Compare</button>
      <div id="compare-results" style="margin-top:1rem"></div></div>
    <div class="panel scroll"><h3>Version history</h3><table><thead><tr><th>Version</th><th>Status</th><th>Origin</th><th>VDB</th><th>Created</th><th>Fingerprint</th></tr></thead><tbody>
      ${versions.map((v) => `<tr><td>${esc(v.version_no)}</td><td>${badge(v.status)}</td><td>${esc(v.origin)}</td>
        <td>${esc(v.teiid_vdb_name)} v${esc(v.teiid_vdb_version)}</td><td class="small">${esc(time(v.created_at))}</td>
        <td><code>${esc((v.content_digest || '').slice(0, 12))}</code></td></tr>`).join('')}</tbody></table></div>`;
  if (versions.length > 1) { $('#from').selectedIndex = 1; $('#to').selectedIndex = 0; }
  $('#compare-button').addEventListener('click', compare);
}

async function compare() {
  const container = $('#compare-results');
  container.innerHTML = '<p class="muted">Comparing artifacts</p>';
  try {
    const d = await api(`/api/ui/diff?from_version=${encodeURIComponent($('#from').value)}&to_version=${encodeURIComponent($('#to').value)}&kind=${encodeURIComponent($('#kind').value)}`);
    if (d.identical) { container.innerHTML = '<p class="muted">The artifact is identical in both versions.</p>'; return; }
    container.innerHTML = `<p class="small muted" style="margin-bottom:0.5rem">${esc(d.added)} line(s) added, ${esc(d.removed)} line(s) removed</p>
      <div class="diff">${d.diff.map((b) => {
        const cls = b.startsWith('+++') || b.startsWith('---') ? 'header-line' : b.startsWith('@@') ? 'hunk' : b.startsWith('+') ? 'added' : b.startsWith('-') ? 'removed' : '';
        return `<div class="${cls}">${esc(b) || ' '}</div>`;
      }).join('')}</div>`;
  } catch (e) { container.innerHTML = `<p style="color:var(--failure)">${esc(e.message)}</p>`; }
}

/* ── page: impact analysis (a preview that changes nothing) ──────────────── */
async function impactPage() {
  await loadTypes();
  const sources = await k(`obdf/${S.oid}/sources`);
  content().innerHTML = `
    <div class="page-header"><div><h1>Impact analysis</h1>
      <p class="muted">Simulate a schema change before it happens. ASCAM computes the affected columns and builds the plan, without changing anything.</p></div></div>
    <form class="panel" id="impact-form">
      <div class="form-row">
        <div><label for="d-op">Change</label><select id="d-op"><option value="add">Add column</option><option value="drop">Drop column</option><option value="rename">Rename column</option></select></div>
        <div><label for="d-source">Source</label><select id="d-source">${sources.map((s) => `<option value="${esc(s.logical_name)}">${esc(s.logical_name)} (${esc(s.dbms)})</option>`).join('')}</select></div>
        <div><label for="d-table">Table</label><input id="d-table" required placeholder="penerima_manfaat"></div>
        <div><label for="d-column">Column in the source</label><input id="d-column" required placeholder="email"></div>
        <div><label for="d-new">New name (rename)</label><input id="d-new"></div>
        <div><label for="d-type">Type (add column)</label><input id="d-type" placeholder="varchar(100)"></div>
      </div>
      <button class="primary" type="submit">Compute impact</button>
    </form>
    <div id="impact-results"></div>`;
  $('#impact-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const body = { operation: $('#d-op').value, source: $('#d-source').value, table: $('#d-table').value.trim(),
      column: $('#d-column').value.trim(), new_column: $('#d-new').value.trim() || null, column_type: $('#d-type').value.trim() || null };
    try {
      const r = await k(`obdf/${S.oid}/impact`, { method: 'POST', body });
      const simulated = { id: '-', status: r.decision, pattern: r.pattern, created_at: new Date().toISOString(), base_spec_version_id: r.spec_version_id,
        reasons: r.reasons, impact: r, actions: r.actions.map((a, i) => ({ seq: i + 1, artifact: a.artifact, operation: a.operation,
          params: Object.fromEntries(Object.entries(a).filter(([key]) => !['artifact', 'operation'].includes(key))) })) };
      S.events['-'] = { structured: { ...body, schema: null } };
      simulated.event_id = '-';
      $('#impact-results').innerHTML = `<div style="margin-top:1.25rem">${proposalDocument(simulated, { decidable: false, preview: true })}</div>`;
    } catch (e) { toast(e.message, 'failure'); }
  });
}

/* ── page: configuration ───────────────────────────────────────────────────── */
async function configurationPage() {
  const [sources, targets, settings, naming] = await Promise.all([
    k(`obdf/${S.oid}/sources`), k(`obdf/${S.oid}/targets`), k(`obdf/${S.oid}/settings`), k(`obdf/${S.oid}/naming-policy`)]);
  content().innerHTML = `
    <div class="page-header"><div><h1>Configuration</h1>
      <p class="muted">The monitored sources, the OBDF components managed by ASCAM, and the adaptation decision rules.</p></div></div>
    <div class="panel"><h3>Adaptation policy</h3>
      <table><tbody>
        <tr><td>${badge('auto')}</td><td>Changes whose impact is fully traced, determined by the DDL, and that change neither the identity, the rows, nor the values of other elements. Applied directly.</td></tr>
        <tr><td>${badge('hitl')}</td><td>Column additions. The name and domain of the new property are a modelling decision, so the proposal of ASCAM waits for your approval and can be revised.</td></tr>
        <tr><td>${badge('notify')}</td><td>Column drops or renames that are not shown to be safe. ASCAM does not change the OBDF; the affected elements are reported on the Notifications page.</td></tr>
      </tbody></table></div>
    <div class="grid-two">
      <div class="panel scroll"><h3>Data sources</h3><table><thead><tr><th>Name</th><th>DBMS</th><th>Default schema</th><th>Monitor topic</th></tr></thead><tbody>
        ${sources.map((s) => `<tr><td>${esc(s.logical_name)}</td><td>${esc(s.dbms)}</td><td>${esc(s.default_schema)}</td><td class="small break"><code>${esc(s.kafka_topic)}</code></td></tr>`).join('')}</tbody></table></div>
      <div class="panel"><h3>Property naming policy</h3><table><tbody>
        <tr><td class="muted">Namespace</td><td><code>${esc((settings.find((p) => p.key === 'ontology.namespace') || {}).value || '-')}</code></td></tr>
        <tr><td class="muted">IRI template</td><td><code>${esc(naming.property_iri_template)}</code></td></tr>
        <tr><td class="muted">On a name collision</td><td>${esc(COLLISION[naming.on_collision] || naming.on_collision)}</td></tr></tbody></table></div>
    </div>
    <div class="panel scroll"><h3>OBDF components</h3><table><thead><tr><th>Kind</th><th>Address</th><th>Enabled</th><th></th></tr></thead><tbody>
      ${targets.map((t) => `<tr><td>${esc(TARGET_KINDS[t.kind] || t.kind)}</td><td class="small"><code>${esc(t.endpoint.host)}:${esc(t.endpoint.port)}${esc(t.endpoint.path || '')}</code></td>
        <td>${t.enabled ? 'yes' : 'no'}</td><td><button class="small" type="button" data-check="${esc(t.id)}">Check</button>
        <span class="small" id="check-${esc(t.id)}"></span></td></tr>`).join('')}</tbody></table></div>`;
  content().querySelectorAll('[data-check]').forEach((b) => b.addEventListener('click', async () => {
    const id = b.dataset.check;
    $(`#check-${id}`).textContent = ' checking';
    try {
      const h = await k(`targets/${id}/check`, { method: 'POST', body: {} });
      $(`#check-${id}`).innerHTML = ` ${h.ok ? '<span class="badge success">Connected</span>' : '<span class="badge failure">Failed</span>'} ${esc(h.latency_ms)} ms`;
    } catch (e) { $(`#check-${id}`).textContent = ` ${e.message}`; }
  }));
}

/* ── page: audit trail ─────────────────────────────────────────────────────── */
async function auditPage() {
  const listing = await k(`obdf/${S.oid}/audit`);
  content().innerHTML = `
    <div class="page-header"><div><h1>Audit trail</h1>
      <p class="muted">Every action on Knowledge, including administrator decisions, syncs, and executions, with who performed it.</p></div></div>
    <div class="panel scroll"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Object</th><th>Details</th></tr></thead><tbody>
      ${listing.map((a) => `<tr><td class="small">${esc(time(a.created_at || a.at))}</td><td>${esc(a.actor)}</td><td>${esc(a.action)}</td>
        <td class="small">${esc(a.object_kind || '')} ${esc(a.object_ref || '')}</td>
        <td class="small muted"><code>${esc(JSON.stringify(a.detail || {}).slice(0, 160))}</code></td></tr>`).join('')}</tbody></table></div>`;
}

/* ── shell, routes, and periodic refresh ───────────────────────────────────── */
const ROUTES = {
  overview: overviewPage, approvals: approvalsPage, changes: changesPage, plans: plansPage,
  versions: versionsPage, impact: impactPage, configuration: configurationPage, audit: auditPage,
  notifications: notificationsPage, lineage: lineagePage,
};

async function showRoute() {
  const name = currentRoute();
  const page = ROUTES[name] || overviewPage;
  document.querySelectorAll('.nav a[data-route]').forEach((a) => {
    if (a.dataset.route === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  clearInterval(S.timer);
  try {
    await page();
    if (name === 'overview' || !ROUTES[name]) S.timer = setInterval(() => overviewPage().catch(() => {}), 5000);
  } catch (e) {
    content().innerHTML = `<div class="panel"><h3>The page cannot be loaded</h3><p style="color:var(--failure)">${esc(e.message)}</p>
      <p class="muted" style="margin-top:0.5rem">Check that the Knowledge Service is running, then reload the page.</p></div>`;
  }
}

async function refreshBadges() {
  try {
    const [r, health] = await Promise.all([api('/api/ui/overview'), api('/api/ui/health')]);
    const count = r.pending.length;
    const pendingBadge = $('#pending-count');
    pendingBadge.textContent = count;
    pendingBadge.hidden = count === 0;
    // only toast when there is a NEW proposal or notification since the previous check
    if (S.pending !== null && count > S.pending) toast(`${count} column-addition proposals are waiting for your approval.`, 'decision', ['#/approvals', 'Review']);
    S.pending = count;
    const notices = r.notifications.filter((n) => !r.pending.some((p) => p.id === n.plan_id)).length;
    const noticeBadge = $('#notification-count');
    noticeBadge.textContent = notices;
    noticeBadge.hidden = notices === 0;
    if (S.unread !== null && notices > S.unread) toast('A schema change could not be adapted automatically.', 'warning', ['#/notifications', 'View']);
    S.unread = notices;
    const v = r.active_version || {};
    $('#active-version').textContent = v.version_no ? `Specification version ${v.version_no}, VDB v${v.teiid_vdb_version}` : 'No active version yet';
    const exe = health.executor || {};
    const button = $('#executor-button');
    if (exe.state) {
      $('#executor-status').textContent = exe.paused ? 'Executor paused' : `Executor ${exe.state === 'running' ? 'running' : exe.state}`;
      button.hidden = false;
      button.textContent = exe.paused ? 'Resume executor' : 'Pause executor';
      button.dataset.action = exe.paused ? 'resume' : 'pause';
    } else {
      $('#executor-status').textContent = 'Executor does not answer';
      button.hidden = true;
    }
  } catch { /* ignored; retried later */ }
}

function showLogin() {
  $('#console').hidden = true;
  $('#login-screen').hidden = false;
  clearInterval(S.timer);
  $('#username').focus();
}

async function startConsole(me) {
  S.user = me.user;
  const listing = await k('obdf');
  S.obdf = listing.find((o) => o.name === me.obdf) || listing[0];
  if (!S.obdf) throw new Error('No OBDF is registered in Knowledge yet.');
  S.oid = S.obdf.id;
  $('#admin-name').textContent = S.user;
  $('#obdf-name').textContent = S.obdf.name;
  $('#login-screen').hidden = true;
  $('#console').hidden = false;
  await refreshBadges();
  await showRoute();
}

document.addEventListener('DOMContentLoaded', async () => {
  $('#login-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    $('#login-message').textContent = '';
    try {
      await api('/api/ui/login', { method: 'POST', body: { username: $('#username').value, password: $('#password').value } });
      $('#password').value = '';
      await startConsole(await api('/api/ui/me'));
    } catch (e) { $('#login-message').textContent = e.message; }
  });
  $('#logout-button').addEventListener('click', async () => {
    await api('/api/ui/logout', { method: 'POST' }).catch(() => {});
    showLogin();
  });
  $('#executor-button').addEventListener('click', async (ev) => {
    const action = ev.currentTarget.dataset.action;
    try {
      await api(`/api/ui/executor/${action}`, { method: 'POST' });
      toast(action === 'pause' ? 'Executor paused. New plans are not applied until it is resumed.' : 'Executor resumed.');
      await refreshBadges();
    } catch (e) { toast(e.message, 'failure'); }
  });
  window.addEventListener('hashchange', showRoute);
  window.addEventListener('resize', () => { if ($('#lineage')) lineageEdges(); });
  setInterval(refreshBadges, 8000);
  try { await startConsole(await api('/api/ui/me')); } catch { showLogin(); }
});
