'use strict';
/* Konsol administrator ASCAM.
   Seluruh data dinamis melewati esc() sebelum masuk ke HTML, karena isi event, rencana, dan
   artefak berasal dari sistem lain dan tidak boleh dipercaya sebagai markup. */

const S = { user: null, obdf: null, oid: null, menunggu: null, belumDibaca: null, tipe: {}, events: {}, rencana: {}, pemutar: null };
const LANGKAH = ['deploy_vdb', 'validate', 'start_ontop', 'switch', 'reload_ontop', 'verify', 'sync', 'rollback'];
const NAMA_LANGKAH = {
  deploy_vdb: 'Deploy VDB', validate: 'Validasi', start_ontop: 'Nyalakan Ontop siaga', switch: 'Pindah koneksi', reload_ontop: 'Muat ulang Ontop',
  verify: 'Verifikasi', sync: 'Sinkronisasi', rollback: 'Pemulihan',
};
const LAPISAN = {
  vdb: { nama: 'VDB (Σ_S)', kelas: 'sigma' },
  r2rml: { nama: 'Mapping (ℳ)', kelas: 'mapping' },
  ontology: { nama: 'Ontologi (𝒯)', kelas: 'ontologi' },
};
const STATUS = {
  pending_approval: ['Menunggu persetujuan', 'keputusan'], approved: ['Disetujui', ''],
  executed: ['Diterapkan', 'berhasil'], failed: ['Gagal', 'gagal'], rejected: ['Ditolak', 'gagal'],
  superseded: ['Digantikan', ''], planned: ['Direncanakan', ''], ignored: ['Diabaikan', ''],
  received: ['Diterima', ''], running: ['Berjalan', 'keputusan'], succeeded: ['Berhasil', 'berhasil'],
  rolled_back: ['Dipulihkan', 'gagal'], active: ['Aktif', 'berhasil'], candidate: ['Kandidat', ''],
  auto: ['Otomatis', ''], hitl: ['Perlu persetujuan', 'keputusan'],
  notify: ['Notifikasi', 'peringatan'], notified: ['Diberitahukan', 'peringatan'], revised: ['Direvisi', ''],
};
const LAPISAN_LINEAGE = [
  { kunci: 'source', judul: 'Kolom sumber', kelas: 'sumber' },
  { kunci: 'foreign', judul: 'Foreign table (Σ_S)', kelas: 'sigma' },
  { kunci: 'view', judul: 'View (Σ_V)', kelas: 'sigma' },
  { kunci: 'mapping', judul: 'TriplesMap (ℳ)', kelas: 'mapping' },
  { kunci: 'ontology', judul: 'Property (𝒯)', kelas: 'ontologi' },
];
const PERAN = {
  literal_value: 'nilai literal', iri_template: 'template IRI', join_key: 'kunci join', sql_predicate: 'predikat SQL',
  dynamic_predicate: 'predikat dinamis', projection_only: 'hanya diproyeksikan',
  maps_to: 'dipetakan ke property', name_in_source: 'NAMEINSOURCE', direct: 'langsung',
  passthrough: 'pass-through', star: 'SELECT *', expression: 'ekspresi', predicate: 'klausa WHERE/JOIN',
};

const JENIS_TARGET = {
  teiid_mgmt: 'Manajemen Teiid', teiid_odbc: 'Teiid (ODBC)', ontop_sparql: 'Endpoint SPARQL Ontop',
  ontop_agent: 'Ontop Agent', kafka: 'Kafka',
};
const BENTROK = { qualify_with_class: 'Tambahkan nama kelas pada IRI', hitl: 'Serahkan ke administrator' };

const $ = (sel) => document.querySelector(sel);
const isi = () => $('#isi');

function esc(nilai) {
  return String(nilai ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
function tanda(status) {
  const [teks, kelas] = STATUS[status] || [status || '-', ''];
  return `<span class="tanda ${kelas}">${esc(teks)}</span>`;
}
function waktu(iso) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('id-ID', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function relatif(iso) {
  if (!iso) return '-';
  const detik = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (detik < 60) return `${detik} detik lalu`;
  if (detik < 3600) return `${Math.round(detik / 60)} menit lalu`;
  if (detik < 86400) return `${Math.round(detik / 3600)} jam lalu`;
  return waktu(iso);
}
function durasi(ms) {
  if (ms === null || ms === undefined) return '-';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} detik`;
}
function pendek(iri) {
  if (!iri) return '-';
  const s = String(iri);
  const i = Math.max(s.lastIndexOf('#'), s.lastIndexOf('/'));
  return i >= 0 ? s.slice(i + 1) : s;
}
function kabar(pesan, jenis = '', tautan = null) {
  const el = $('#kabar');
  el.className = `kabar ${jenis}`;
  el.innerHTML = esc(pesan) + (tautan ? ` <a href="${esc(tautan[0])}">${esc(tautan[1])}</a>` : '');
  el.hidden = false;
  clearTimeout(kabar.t);
  kabar.t = setTimeout(() => { el.hidden = true; }, 6000);
}

async function api(path, opsi = {}) {
  const kepala = { 'X-ASCAM-UI': '1' };
  if (opsi.body !== undefined) kepala['Content-Type'] = 'application/json';
  const res = await fetch(path, {
    method: opsi.method || 'GET', headers: kepala, credentials: 'same-origin',
    body: opsi.body !== undefined ? JSON.stringify(opsi.body) : undefined,
  });
  if (res.status === 401 && !path.startsWith('/api/ui/login')) {
    tampilkanMasuk();
    throw new Error('Sesi berakhir. Silakan masuk kembali.');
  }
  const teks = await res.text();
  let data = null;
  try { data = teks ? JSON.parse(teks) : null; } catch { data = { detail: teks }; }
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === 'string' ? detail : `Permintaan gagal (HTTP ${res.status})`);
  }
  return data;
}
const k = (path, opsi) => api(`/api/k/${path}`, opsi);

/* ── penjelasan kejadian dan tindakan dalam kalimat biasa ─────────────────── */
function objekEvent(s) {
  const lokasi = [s.schema, s.table].filter(Boolean).join('.');
  return `${s.source || '?'}: ${lokasi}`;
}
function kalimatEvent(s) {
  if (!s) return 'Perubahan skema';
  const lokasi = objekEvent(s);
  switch (s.operation) {
    case 'add': return `Kolom baru ${s.column}${s.column_type ? ` (${s.column_type})` : ''} pada ${lokasi}`;
    case 'drop': return `Kolom ${s.column} dihapus dari ${lokasi}`;
    case 'rename': return `Kolom ${s.column} diganti nama menjadi ${s.new_column} pada ${lokasi}`;
    default: return `Perubahan yang tidak didukung pada ${lokasi}`;
  }
}
function rangeUntuk(tipe) {
  if (!tipe) return null;
  const dasar = String(tipe).toLowerCase().split('(')[0].trim();
  return S.tipe[String(tipe).toLowerCase()] || S.tipe[dasar] || null;
}
function jelaskan(a, plan) {
  const p = a.params || {};
  const tipeKolom = (plan.actions.find((x) => x.params && x.params.column_type) || {}).params?.column_type;
  switch (`${a.artifact}:${a.operation}`) {
    case 'vdb:add_column':
      return [`Tambahkan kolom ${p.column} pada tabel ${p.table}`, `Model VDB ${p.model}, tipe sumber ${p.column_type || '-'}; ditulis sebagai ALTER FOREIGN TABLE pada VDB versi baru`];
    case 'vdb:drop_column':
      return [`Hapus kolom ${p.column} dari tabel ${p.table}`, `Model VDB ${p.model}; versi VDB lama tetap tersedia untuk pemulihan`];
    case 'vdb:alter_view':
      return [`Keluarkan ${(p.removed_columns || []).join(', ')} dari view ${p.model}.${p.table}`, `Definisi baru: ${p.body}; ditulis sebagai ALTER VIEW pada VDB versi baru`];
    case 'vdb:set_name_in_source':
      return [`Arahkan ${p.table}.${p.column} ke nama baru di sumber, ${p.name_in_source}`, 'Nama kolom Teiid tidak berubah, sehingga mapping dan ontologi tetap utuh (strategi alias)'];
    case 'ontology:add_datatype_property': {
      const range = rangeUntuk(tipeKolom);
      return [`Tambahkan property ${pendek(p.iri)}`, `IRI ${p.iri}; domain ${(p.domain || []).map(pendek).join(', ') || '-'}; range ${range || 'mengikuti pemetaan tipe'}`];
    }
    case 'ontology:deprecate_property':
      return [`Tandai ${pendek(p.predicate_iri)} sebagai usang`, 'Deklarasinya tetap ada dengan owl:deprecated, agar kueri lama tidak gagal'];
    case 'ontology:keep_property':
      return [`Pertahankan ${pendek(p.predicate_iri)}`, 'Property ini masih dipakai pemetaan lain'];
    case 'r2rml:add_predicate_object_map':
      return [`Petakan kolom ${p.column} ke property ${pendek(p.predicate_iri)}`, `Pada TriplesMap ${pendek(p.triples_map_iri) || p.table}; ditambahkan sebagai blok terkelola tanpa menulis ulang mapping`];
    case 'r2rml:remove_predicate_object_map':
      return [`Hapus pemetaan ${pendek(p.predicate_iri)}`, `Dari ${pendek(p.triples_map_iri) || 'seluruh TriplesMap'}`];
    case 'r2rml:rewrite_logical_table':
      return [`Keluarkan kolom ${p.column} dari kueri logical table`, `Pada ${pendek(p.triples_map_iri)}`];
    default:
      return [`${a.operation}`, JSON.stringify(p)];
  }
}
function daftarTindakan(plan) {
  if (!plan.actions || !plan.actions.length) {
    return '<p class="redup">Tidak ada tindakan otomatis. Perubahan ini harus ditangani administrator secara manual.</p>';
  }
  return `<ul class="tindakan">${plan.actions.map((a) => {
    const [judul, rinci] = jelaskan(a, plan);
    const l = LAPISAN[a.artifact] || { nama: a.artifact, kelas: '' };
    return `<li class="${esc(a.artifact)}"><span class="tanda ${l.kelas}">${esc(l.nama)}</span>
      <div><strong>${esc(judul)}</strong><div class="rinci">${esc(rinci)}</div></div></li>`;
  }).join('')}</ul>`;
}
function daftarAlasan(alasan) {
  if (!alasan || !alasan.length) return '<p class="redup">Tidak ada catatan.</p>';
  return `<ul class="alasan">${alasan.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>`;
}
function tabelPemakaian(impact) {
  const baris = [];
  for (const t of (impact && impact.targets) || []) {
    for (const u of t.usages || []) {
      baris.push(`<tr><td>${esc(t.table)}.${esc(t.column)}</td><td>${esc(pendek(u.triples_map))}</td>
        <td>${esc(u.role)}</td><td>${esc(pendek(u.predicate_iri))}</td><td>${esc(u.weakest_link)}</td></tr>`);
    }
  }
  if (!baris.length) return '<p class="redup">Kolom ini belum dipakai mapping mana pun.</p>';
  return `<div class="gulir"><table><thead><tr><th>Kolom Teiid</th><th>TriplesMap</th><th>Peran</th>
    <th>Predikat</th><th>Tepi terlemah</th></tr></thead><tbody>${baris.join('')}</tbody></table></div>`;
}

/* ── pemuatan data bersama ─────────────────────────────────────────────────── */
async function muatTipe() {
  if (Object.keys(S.tipe).length) return;
  try {
    for (const t of await k(`obdf/${S.oid}/type-mappings`)) S.tipe[t.native_type.toLowerCase()] = t.xsd_datatype;
  } catch { /* opsional */ }
}
async function muatEvents() {
  for (const e of await k(`obdf/${S.oid}/events?limit=200`)) S.events[e.id] = e;
}

/* ── halaman: ringkasan ────────────────────────────────────────────────────── */
function batangLangkah(eksekusi) {
  const langkah = (eksekusi.steps || []).filter((s) => s.detail && s.detail.duration_ms);
  const total = langkah.reduce((n, s) => n + s.detail.duration_ms, 0) || 1;
  const potong = langkah.map((s) => `<span class="l-${esc(s.name)}" style="width:${(100 * s.detail.duration_ms / total).toFixed(2)}%"
      title="${esc(NAMA_LANGKAH[s.name] || s.name)}: ${esc(durasi(s.detail.duration_ms))}"></span>`).join('');
  return `<div class="batang" role="img" aria-label="Durasi langkah eksekusi">${potong}</div>`;
}
function legendaLangkah() {
  return `<div class="legenda">${LANGKAH.map((n) => `<span><i class="l-${n}"></i>${esc(NAMA_LANGKAH[n])}</span>`).join('')}</div>`;
}
function baris_eksekusi(e) {
  return `<tr><td>${esc(e.plan_id)}</td><td>${tanda(e.status)}</td><td>${esc(waktu(e.started_at))}</td>
    <td>${esc(durasi(e.timings && e.timings.total_ms))}</td><td>${batangLangkah(e)}</td></tr>`;
}

function ruteSekarang() {
  return (location.hash.replace(/^#\//, '') || 'ringkasan').split('?')[0];
}
async function halamanRingkasan() {
  const [r, sehat] = await Promise.all([api('/api/ui/overview'), api('/api/ui/health')]);
  // pembaruan berkala yang selesai setelah pengguna pindah halaman tidak boleh menimpa halaman baru
  if (RUTE[ruteSekarang()] && ruteSekarang() !== 'ringkasan') return;
  const ev = r.events[0];
  const eks = r.executions[0];
  const exe = sehat.executor || {};
  const versi = r.active_version || {};
  const pemberitahuan = r.notifications.filter((n) => !r.pending.some((p) => p.id === n.plan_id)).length;
  const tahap = [
    { nama: 'Monitor', nilai: r.events.length ? relatif(ev.received_at) : '-',
      ket: ev ? kalimatEvent(ev.structured) : 'Belum ada perubahan skema', tautan: '#/perubahan' },
    { nama: 'Analisis', nilai: ev ? (STATUS[ev.status] || [ev.status])[0] : '-',
      ket: ev && ev.ignore_reason ? ev.ignore_reason : 'Dampak dihitung dari lineage kolom', tautan: '#/adaptasi' },
    { nama: 'Keputusan', nilai: `${r.pending.length} menunggu`, perhatian: r.pending.length > 0 || pemberitahuan > 0,
      ket: [r.pending.length ? 'Penambahan kolom menunggu persetujuan Anda' : 'Tidak ada usulan yang menunggu',
        pemberitahuan ? `${pemberitahuan} notifikasi perubahan yang tidak diadaptasi` : ''].filter(Boolean).join('; '),
      tautan: r.pending.length || !pemberitahuan ? '#/persetujuan' : '#/notifikasi' },
    { nama: 'Eksekusi', nilai: eks ? (STATUS[eks.status] || [eks.status])[0] : '-', bermasalah: eks && ['failed', 'rolled_back'].includes(eks.status),
      ket: eks ? `Rencana ${eks.plan_id}, ${durasi(eks.timings && eks.timings.total_ms)}${exe.paused ? '; executor dijeda' : ''}` : 'Belum ada eksekusi', tautan: '#/adaptasi' },
    { nama: 'Pengetahuan', nilai: versi.version_no ? `Versi ${versi.version_no}` : '-',
      ket: versi.teiid_vdb_name ? `VDB ${versi.teiid_vdb_name} v${versi.teiid_vdb_version}, ${versi.teiid_connection_type || ''}` : '', tautan: '#/versi' },
  ];
  const komponen = [
    ['Knowledge', sehat.knowledge, (d) => d.status || (d._galat ? d._galat : 'siap')],
    ['Orchestrator', sehat.orchestrator, (d) => d.state ? `${d.state}; ${d.messages ?? 0} pesan, ${d.events_sent ?? 0} event, ${d.skipped ?? 0} dilewati, ${d.failures ?? 0} gagal` : d._galat],
    ['Executor', sehat.executor, (d) => d.state ? `${d.paused ? 'dijeda' : d.state}; ${d.executed ?? 0} diterapkan, ${d.rolled_back ?? 0} dipulihkan, ${d.failed ?? 0} gagal` : d._galat],
    ['Ontop Agent', sehat.agent, (d) => d.status ? `${d.status}; artefak ${Object.entries(d.artifacts || {}).map(([a, b]) => `${a} ${b ? 'ada' : 'hilang'}`).join(', ')}` : d._galat],
  ];
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Ringkasan</h1>
      <p class="redup">Keadaan siklus adaptasi ${esc(r.obdf.name)} dan komponen ASCAM. Diperbarui otomatis setiap lima detik.</p></div></div>
    <nav class="siklus" aria-label="Siklus adaptasi">${tahap.map((t) => `
      <a class="tahap ${t.perhatian ? 'perhatian' : ''} ${t.bermasalah ? 'bermasalah' : ''}" href="${t.tautan}">
        <span class="nama">${esc(t.nama)}</span><span class="nilai">${esc(t.nilai)}</span>
        <span class="keterangan">${esc(t.ket)}</span></a>`).join('')}</nav>
    <div class="grid-dua" style="margin-top:1.25rem">
      <div class="panel"><h3>Komponen ASCAM</h3><table><tbody>${komponen.map(([nama, d, f]) => {
        const ok = d && !d._galat && !['failed', 'degraded'].includes(d.state) && d.status !== 'degraded';
        return `<tr><td><strong>${esc(nama)}</strong></td><td>${ok ? '<span class="tanda berhasil">Sehat</span>' : '<span class="tanda gagal">Bermasalah</span>'}</td>
          <td class="kecil redup">${esc(f(d || {}) || '-')}</td></tr>`;
      }).join('')}</tbody></table>
      <p style="margin-top:1rem"><button class="kecil" id="periksa-target" type="button">Periksa koneksi Teiid, Ontop, dan Kafka</button></p>
      <div id="hasil-periksa"></div></div>
      <div class="panel"><h3>Perubahan skema terakhir</h3>${r.events.length ? `<table><tbody>${r.events.map((e) => `
        <tr><td class="kecil redup waktu">${esc(relatif(e.received_at))}</td><td>${esc(kalimatEvent(e.structured))}</td><td>${tanda(e.status)}</td></tr>`).join('')}
        </tbody></table>` : '<p class="kosong">Belum ada perubahan skema yang terdeteksi.</p>'}</div>
    </div>
    <div class="panel"><h3>Eksekusi terakhir</h3>${r.executions.length ? `<div class="gulir"><table>
      <thead><tr><th>Rencana</th><th>Status</th><th>Mulai</th><th>Total</th><th>Durasi per langkah</th></tr></thead>
      <tbody>${r.executions.map(baris_eksekusi).join('')}</tbody></table></div>${legendaLangkah()}` : '<p class="kosong">Belum ada rencana yang dieksekusi.</p>'}</div>`;
  $('#periksa-target').addEventListener('click', periksaTarget);
}

async function periksaTarget() {
  const tombol = $('#periksa-target');
  tombol.disabled = true;
  tombol.textContent = 'Memeriksa koneksi';
  try {
    const hasil = await k(`obdf/${S.oid}/checks`, { method: 'POST', body: {} });
    $('#hasil-periksa').innerHTML = `<table style="margin-top:0.75rem"><tbody>${hasil.map((h) => `
      <tr><td>${h.ok ? '<span class="tanda berhasil">Terhubung</span>' : '<span class="tanda gagal">Gagal</span>'}</td>
      <td class="kecil">${esc(h.detail && h.detail.summary)}</td><td class="kecil redup">${esc(h.latency_ms)} ms</td></tr>`).join('')}</tbody></table>`;
  } catch (e) { kabar(e.message, 'gagal'); }
  tombol.disabled = false;
  tombol.textContent = 'Periksa koneksi Teiid, Ontop, dan Kafka';
}

/* ── halaman: persetujuan (hanya penambahan kolom, ADR-0024) ───────────────── */
function camel(kolom) {
  return String(kolom || '').split('_').map((b, i) => (i ? b.charAt(0).toUpperCase() + b.slice(1).toLowerCase() : b)).join('');
}
function tautanLineage(plan) {
  const s = (S.events[plan.event_id] || {}).structured;
  if (!s || !s.table) return '';
  const q = new URLSearchParams({ sumber: s.source || '', tabel: s.table, kolom: s.column || '' });
  return `<p style="margin-top:0.75rem"><a href="#/lineage?${esc(q.toString())}">Lihat jalurnya pada lineage kolom</a></p>`;
}
function daftarTerdampak(plan, { tautan = true } = {}) {
  const a = plan.impact && plan.impact.affected;
  if (!a) return '';
  const kelompok = [['columns', 'Kolom (Σ_S)', 'sigma'], ['views', 'View (Σ_V)', 'sigma'],
    ['triples_maps', 'TriplesMap (ℳ)', 'mapping'], ['predicates', 'Property (𝒯)', 'ontologi']]
    .filter(([kunci]) => a[kunci] && a[kunci].length);
  if (!kelompok.length) return '';
  return `<section><h3>Elemen OBDF yang terdampak</h3><ul class="terdampak">${kelompok.map(([kunci, judul, kelas]) => `
    <li><span class="tanda ${kelas}">${esc(judul)}</span><span>${a[kunci].map((x) => esc(['columns', 'views'].includes(kunci) ? x : pendek(x))).join(', ')}</span></li>`).join('')}</ul>
    ${tautan ? tautanLineage(plan) : ''}</section>`;
}
function formulirProperty(plan) {
  const o = plan.impact && plan.impact.add_options;
  if (!o) return '';
  if (!o.triples_maps || !o.triples_maps.length || !o.namespace || !o.iri_template) {
    return `<section><h3>Property baru di ontologi</h3><p class="redup">Tidak ada property yang dapat ditambahkan:
      ${!o.triples_maps || !o.triples_maps.length ? 'tidak ada TriplesMap yang otomatis memuat kolom baru, sehingga persetujuan hanya menambah kolom pada VDB.' : 'kebijakan penamaan atau namespace ontologi belum diatur.'}</p></section>`;
  }
  const id = esc(plan.id);
  const domain = o.proposed_domain || (o.classes || [])[0] || '';
  return `<section><h3>Property baru di ontologi</h3>
    <p class="redup kecil" style="margin-bottom:0.75rem">${o.proposed_iri ? 'Usulan ASCAM dari kebijakan penamaan. Ubah bila perlu; persetujuan dengan nama atau domain lain dicatat sebagai rencana revisi.'
      : 'ASCAM tidak dapat mengusulkan nama yang bebas bentrok. Tentukan nama property sebelum menyetujui.'}</p>
    <div class="baris-formulir">
      <div><label for="prop-${id}">Nama property</label>
        <input id="prop-${id}" data-property="${id}" value="${esc(o.proposed_iri ? pendek(o.proposed_iri) : '')}" placeholder="${esc(camel(o.column))}" maxlength="100" autocomplete="off" spellcheck="false"></div>
      <div><label for="dom-${id}">Domain (kelas subjek)</label>${(o.classes || []).length
        ? `<select id="dom-${id}" data-property="${id}">${o.classes.map((c) => `<option value="${esc(c)}" ${c === domain ? 'selected' : ''}>${esc(pendek(c))}</option>`).join('')}</select>`
        : '<p class="kecil redup" style="padding-top:0.5rem">TriplesMap pengekspos tidak memberi kelas, sehingga property tanpa domain.</p>'}</div>
    </div>
    <p class="kecil pratinjau-property" id="pratinjau-${id}"></p></section>`;
}
function pilihanProperty(id) {
  const plan = S.rencana[id];
  const o = plan && plan.impact && plan.impact.add_options;
  const masukan = $(`#prop-${id}`);
  if (!o || !masukan) return null;
  const pilih = $(`#dom-${id}`);
  return { o, nama: masukan.value.trim(), domain: pilih ? pilih.value : null };
}
function pratinjauProperty(id) {
  const p = pilihanProperty(id);
  if (!p) return;
  const el = $(`#pratinjau-${id}`);
  if (!/^[A-Za-z_][A-Za-z0-9_-]{0,99}$/.test(p.nama)) {
    el.innerHTML = '<span style="color:var(--gagal)">Nama diawali huruf atau garis bawah, lalu huruf, angka, garis bawah, atau tanda hubung.</span>';
    return;
  }
  const iri = p.o.iri_template.replace('{namespace}', p.o.namespace).replace('{column_camel}', p.nama).replace('{column}', p.nama);
  const tm = p.o.triples_maps.filter((t) => !p.domain || !t.classes.length || t.classes.includes(p.domain)).map((t) => pendek(t.iri));
  el.innerHTML = `IRI <code>${esc(iri)}</code>; kolom ${esc(p.o.column)} dipetakan pada ${tm.length ? tm.map(esc).join(', ') : '<strong>tidak ada TriplesMap</strong>'}.`;
}

function dokumenUsulan(plan, { dapatDiputuskan, pratinjau = false }) {
  const ev = S.events[plan.event_id];
  const struktur = ev ? ev.structured : null;
  const notifikasi = plan.decision === 'notify' || plan.status === 'notify';
  const judulTindakan = notifikasi ? 'Tindakan ASCAM' : ['executed', 'failed'].includes(plan.status) ? 'Yang dilakukan ASCAM' : 'Yang akan dilakukan ASCAM';
  const tindakan = notifikasi && !(plan.actions || []).length
    ? '<p class="redup">ASCAM tidak mengubah OBDF untuk perubahan ini. Perbaiki spesifikasi secara manual pada elemen yang terdampak, lalu jalankan sinkronisasi agar Knowledge memuat versi barunya.</p>'
    : daftarTindakan(plan);
  const judulAlasan = dapatDiputuskan ? 'Mengapa butuh keputusan Anda' : notifikasi ? 'Mengapa tidak diadaptasi otomatis' : 'Catatan analisis';
  return `<article class="usulan ${notifikasi ? 'pemberitahuan' : ''}" id="usulan-${esc(plan.id)}">
    <header><h2>${esc(kalimatEvent(struktur))}</h2>${tanda(plan.status)}
      <span class="kecil redup">${pratinjau ? `Pola ${esc(plan.pattern || '-')}, dihitung terhadap versi spesifikasi ${esc(plan.base_spec_version_id)}`
        : `Rencana ${esc(plan.id)}${plan.revision_of ? ` (revisi rencana ${esc(plan.revision_of)})` : ''}, pola ${esc(plan.pattern || '-')}, disusun ${esc(relatif(plan.created_at))} di atas versi spesifikasi ${esc(plan.base_spec_version_id)}`}</span></header>
    <section><h3>${judulTindakan}</h3>${tindakan}</section>
    ${dapatDiputuskan ? formulirProperty(plan) : ''}
    ${plan.reasons && plan.reasons.length ? `<section><h3>${judulAlasan}</h3>${daftarAlasan(plan.reasons)}</section>` : ''}
    ${daftarTerdampak(plan)}
    ${plan.impact && plan.impact.targets && plan.impact.targets.length ? `<section><h3>Pemakaian kolom saat ini</h3>${tabelPemakaian(plan.impact)}</section>` : ''}
    ${dapatDiputuskan ? `<section class="keputusan-admin">
      <div><label for="catatan-${esc(plan.id)}">Catatan keputusan (disimpan di jejak audit)</label>
        <textarea id="catatan-${esc(plan.id)}" placeholder="Contoh: nama property sudah sesuai kaidah penamaan"></textarea></div>
      <div class="tombol-grup">
        <button class="tolak" type="button" data-putuskan="reject" data-plan="${esc(plan.id)}">Tolak</button>
        <button class="setujui" type="button" data-putuskan="approve" data-plan="${esc(plan.id)}">Setujui dan terapkan</button>
      </div></section>` : pratinjau ? '<p class="kecil redup" style="margin-top:1rem">Pratinjau. Tidak ada yang diubah pada OBDF.</p>'
      : plan.decided_by ? `<p class="kecil redup" style="margin-top:1rem">Diputuskan oleh ${esc(plan.decided_by)} ${esc(relatif(plan.decided_at))}</p>` : ''}
  </article>`;
}

async function halamanPersetujuan() {
  await Promise.all([muatEvents(), muatTipe()]);
  const [menunggu, semua] = await Promise.all([k(`obdf/${S.oid}/plans?status=pending_approval`),
    k(`obdf/${S.oid}/plans?limit=40`)]);
  for (const p of menunggu) S.rencana[p.id] = p;
  const diputuskan = semua.filter((p) => p.decided_by && p.decision === 'hitl' && p.status !== 'revised').slice(0, 8);
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Persetujuan</h1>
      <p class="redup">Penambahan kolom menambah property baru ke ontologi, sehingga selalu menunggu keputusan Anda. Periksa nama dan domain property serta tindakan pada setiap lapisan OBDF, lalu setujui atau tolak. Perubahan yang tidak dapat diadaptasi otomatis tidak muncul di sini, melainkan di halaman Notifikasi.</p></div></div>
    ${menunggu.length ? menunggu.map((p) => dokumenUsulan(p, { dapatDiputuskan: true })).join('')
      : '<div class="panel"><p class="kosong">Tidak ada usulan yang menunggu keputusan. Penambahan kolom pada tabel yang difederasikan akan muncul di sini.</p></div>'}
    ${diputuskan.length ? `<div class="panel" style="margin-top:1.25rem"><h3>Keputusan terakhir</h3><table>
      <thead><tr><th>Rencana</th><th>Perubahan</th><th>Keputusan</th><th>Oleh</th><th>Waktu</th></tr></thead><tbody>
      ${diputuskan.map((p) => `<tr><td>${esc(p.id)}${p.revision_of ? ` <span class="kecil redup">revisi ${esc(p.revision_of)}</span>` : ''}</td><td>${esc(kalimatEvent((S.events[p.event_id] || {}).structured))}</td>
        <td>${tanda(p.status)}</td><td>${esc(p.decided_by)}</td><td>${esc(waktu(p.decided_at))}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
  isi().querySelectorAll('[data-putuskan]').forEach((b) => b.addEventListener('click', putuskan));
  isi().querySelectorAll('[data-property]').forEach((el) => el.addEventListener('input', () => pratinjauProperty(el.dataset.property)));
  menunggu.forEach((p) => pratinjauProperty(String(p.id)));
}

async function putuskan(ev) {
  const tombol = ev.currentTarget;
  const id = tombol.dataset.plan;
  const aksi = tombol.dataset.putuskan;
  const catatan = $(`#catatan-${id}`).value.trim();
  if (aksi === 'reject' && !catatan) {
    kabar('Tuliskan alasan penolakan pada catatan keputusan.', 'gagal');
    $(`#catatan-${id}`).focus();
    return;
  }
  const body = { note: catatan || null };
  const pilihan = aksi === 'approve' ? pilihanProperty(id) : null;
  if (pilihan) {
    if (!pilihan.nama) {
      kabar('Tentukan nama property sebelum menyetujui.', 'gagal');
      $(`#prop-${id}`).focus();
      return;
    }
    body.property_name = pilihan.nama;
    body.domain = pilihan.domain;
  }
  isi().querySelectorAll(`[data-plan="${id}"]`).forEach((b) => { b.disabled = true; });
  try {
    const hasil = await k(`plans/${id}/${aksi}`, { method: 'POST', body });
    kabar(aksi === 'reject' ? 'Ditolak. Rencana tidak akan diterapkan.'
      : hasil.revision_of ? `Disetujui dengan revisi sebagai rencana ${hasil.id}. Executor akan menerapkannya dalam beberapa detik.`
        : 'Disetujui. Executor akan menerapkan rencana ini dalam beberapa detik.',
    aksi === 'approve' ? 'keputusan' : '', aksi === 'approve' ? ['#/adaptasi', 'Pantau eksekusi'] : null);
    await segarkanLencana();
    await halamanPersetujuan();
  } catch (e) {
    kabar(e.message, 'gagal');
    isi().querySelectorAll(`[data-plan="${id}"]`).forEach((b) => { b.disabled = false; });
  }
}

/* ── halaman: notifikasi (DROP/RENAME yang tidak aman, ADR-0024) ───────────── */
async function halamanNotifikasi() {
  const semua = (location.hash.split('?')[1] || '').includes('semua=1');
  await muatEvents();
  const [daftar, rencana] = await Promise.all([
    k(`obdf/${S.oid}/notifications?limit=100${semua ? '' : '&unread=true'}`), k(`obdf/${S.oid}/plans?limit=200`)]);
  for (const p of rencana) S.rencana[p.id] = p;
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Notifikasi</h1>
      <p class="redup">Perubahan skema yang tidak dapat dibuktikan aman tidak diadaptasi dan tidak perlu disetujui. ASCAM memberi tahu elemen OBDF yang terdampak; perbaiki spesifikasinya secara manual, lalu jalankan sinkronisasi.</p></div>
      <div class="aksi"><a class="tombol" href="#/notifikasi${semua ? '' : '?semua=1'}">${semua ? 'Hanya yang belum dibaca' : 'Tampilkan semua'}</a>
        <button class="utama-aksi" id="tombol-sync" type="button">Jalankan sinkronisasi</button></div></div>
    ${daftar.length ? daftar.map((n) => kartuNotifikasi(n, S.rencana[n.plan_id])).join('')
      : `<div class="panel"><p class="kosong">${semua ? 'Belum ada notifikasi.' : 'Tidak ada notifikasi yang belum dibaca.'}</p></div>`}`;
  isi().querySelectorAll('[data-baca]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await k(`notifications/${b.dataset.baca}/ack`, { method: 'POST', body: {} });
      await segarkanLencana();
      await halamanNotifikasi();
    } catch (e) { kabar(e.message, 'gagal'); b.disabled = false; }
  }));
  $('#tombol-sync').addEventListener('click', async (ev) => {
    const tombol = ev.currentTarget;
    tombol.disabled = true;
    try {
      const h = await k(`obdf/${S.oid}/sync`, { method: 'POST', body: {} });
      kabar(h.changed ? `Sinkronisasi selesai: versi spesifikasi ${h.version_no} kini aktif.` : 'Sinkronisasi selesai: spesifikasi OBDF tidak berubah.');
      await segarkanLencana();
    } catch (e) { kabar(e.message, 'gagal'); }
    tombol.disabled = false;
  });
}

function kartuNotifikasi(n, plan) {
  const s = plan ? (S.events[plan.event_id] || {}).structured : null;
  const tindak = !plan ? '' : plan.status === 'pending_approval'
    ? '<a class="tombol" href="#/persetujuan">Tinjau usulan</a>'
    : plan.decision === 'notify' && s && s.table
      ? `<a class="tombol" href="#/lineage?${esc(new URLSearchParams({ sumber: s.source || '', tabel: s.table, kolom: s.column || '' }).toString())}">Lihat lineage</a>` : '';
  const badan = plan && plan.decision === 'notify'
    ? `${daftarTerdampak(plan, { tautan: false })}${plan.reasons && plan.reasons.length ? `<section><h3>Mengapa tidak diadaptasi otomatis</h3>${daftarAlasan(plan.reasons)}</section>` : ''}`
    : `<p style="margin-top:0.75rem">${esc(n.message)}</p>`;
  return `<article class="usulan ${plan && plan.decision === 'notify' ? 'pemberitahuan' : ''}">
    <header><h2>${esc(s ? kalimatEvent(s) : 'Pemberitahuan')}</h2>${plan ? tanda(plan.status) : tanda(n.severity)}
      <span class="kecil redup">${esc(relatif(n.created_at))}${plan ? `, rencana ${esc(plan.id)}` : ''}${n.acknowledged ? `; dibaca oleh ${esc(n.acknowledged_by)}` : ''}</span></header>
    ${badan}
    <div class="tombol-grup" style="margin-top:1rem">${tindak}${n.acknowledged ? '' : `<button type="button" data-baca="${esc(n.id)}">Tandai sudah dibaca</button>`}</div>
  </article>`;
}

/* ── halaman: lineage kolom spesifikasi aktif ──────────────────────────────── */
function lapisanSimpul(n) {
  if (n.layer === 'sigma') return n.kind === 'view_column' ? 'view' : 'foreign';
  return n.layer;
}
async function halamanLineage() {
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  const data = await k(`obdf/${S.oid}/lineage`);
  const simpul = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
  const tabel = [...new Set(data.paths.map((p) => { const n = simpul[p.nodes[0]]; return `${n.source}|${n.table}`; }))].sort();
  const pilihTabel = q.get('sumber') && q.get('tabel') ? `${q.get('sumber')}|${q.get('tabel')}` : tabel[0];
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Lineage kolom</h1>
      <p class="redup">Jalur setiap kolom sumber sampai property ontologi pada versi spesifikasi ${esc(data.version_no)} yang sedang aktif. Pilih sebuah simpul untuk menyorot semua jalur yang melewatinya; kumpulan jalur itulah yang terdampak bila kolomnya dihapus atau diganti nama.</p></div></div>
    <div class="panel"><div class="baris-formulir" style="margin-bottom:0">
      <div><label for="l-tabel">Tabel sumber</label><select id="l-tabel">
        ${tabel.map((t) => { const [a, b] = t.split('|'); return `<option value="${esc(t)}" ${t === pilihTabel ? 'selected' : ''}>${esc(a)}: ${esc(b)}</option>`; }).join('')}
        <option value="*" ${pilihTabel === '*' ? 'selected' : ''}>Semua tabel</option></select></div>
      <div><label for="l-kolom">Sorot kolom sumber</label><select id="l-kolom"></select></div>
    </div></div>
    <div class="panel lineage-panel"><div class="lineage" id="lineage"></div>${legendaLineage()}</div>
    <div class="panel" id="lineage-rincian"></div>`;
  const gambar = () => {
    const t = $('#l-tabel').value;
    const jalur = data.paths.filter((p) => { const n = simpul[p.nodes[0]]; return t === '*' || `${n.source}|${n.table}` === t; });
    const kolom = [...new Set(jalur.map((p) => simpul[p.nodes[0]].column))].sort();
    const sorot = q.get('kolom') && kolom.includes(q.get('kolom')) ? q.get('kolom') : '';
    q.delete('kolom');
    $('#l-kolom').innerHTML = `<option value="">Tidak ada</option>${kolom.map((c) => `<option value="${esc(c)}" ${c === sorot ? 'selected' : ''}>${esc(c)}</option>`).join('')}`;
    gambarLineage(jalur, simpul, data.edges);
    const awal = jalur.find((p) => simpul[p.nodes[0]].column === sorot);
    sorotLineage(awal ? awal.nodes[0] : null);
  };
  $('#l-tabel').addEventListener('change', gambar);
  $('#l-kolom').addEventListener('change', () => {
    const c = $('#l-kolom').value;
    const awal = S.lineage.jalur.find((p) => S.lineage.simpul[p.nodes[0]].column === c);
    sorotLineage(c && awal ? awal.nodes[0] : null);
  });
  gambar();
}

function legendaLineage() {
  return `<div class="legenda">
    <span><i class="garis-l"></i>Pass-through atau tepi langsung</span>
    <span><i class="garis-l lemah"></i>Ekspresi atau predikat SQL (tidak aman untuk adaptasi otomatis)</span>
    <span><i class="kotak-l tak-terpakai"></i>Kolom yang tidak dipetakan ke ontologi</span>
    <span><i class="kotak-l usang"></i>Property usang (owl:deprecated)</span></div>`;
}

const LEMAH = new Set(['expression', 'predicate', 'iri_template', 'join_key', 'sql_predicate', 'dynamic_predicate']);
function gambarLineage(jalur, simpul, edges) {
  const wadah = $('#lineage');
  const dipakai = new Set(jalur.flatMap((p) => p.nodes));
  const urutan = new Map();
  jalur.forEach((p) => p.nodes.forEach((id) => { if (!urutan.has(id)) urutan.set(id, urutan.size); }));
  const kolomTakTerpakai = new Set(jalur.filter((p) => p.role === null).map((p) => p.nodes[1]));
  wadah.innerHTML = `<svg class="lineage-garis" aria-hidden="true"></svg>${LAPISAN_LINEAGE.map((l) => {
    const isiLapisan = [...dipakai].filter((id) => lapisanSimpul(simpul[id]) === l.kunci).sort((a, b) => urutan.get(a) - urutan.get(b));
    return `<div class="lineage-kolom"><h3 class="kecil">${esc(l.judul)}</h3>${isiLapisan.map((id) => {
      const n = simpul[id];
      const label = n.kind === 'source_column' ? n.column : n.kind.endsWith('_column') ? `${n.table.split('.').pop()}.${n.column}` : n.label;
      const kelas = [l.kelas, kolomTakTerpakai.has(id) ? 'tak-terpakai' : '', n.deprecated ? 'usang' : ''].join(' ');
      return `<button type="button" class="simpul ${kelas}" data-simpul="${esc(id)}" title="${esc(n.iri || n.label)}">${esc(label)}</button>`;
    }).join('') || '<p class="kecil redup">-</p>'}</div>`;
  }).join('')}`;
  S.lineage = { jalur, simpul, edges: edges.filter((e) => dipakai.has(e.from) && dipakai.has(e.to)), terpilih: null };
  wadah.querySelectorAll('[data-simpul]').forEach((b) => b.addEventListener('click', () => {
    sorotLineage(S.lineage.terpilih === b.dataset.simpul ? null : b.dataset.simpul);
  }));
  garisLineage();
}

function garisLineage() {
  const wadah = $('#lineage');
  if (!wadah || !S.lineage) return;
  const svg = wadah.querySelector('svg');
  const kotak = wadah.getBoundingClientRect();
  svg.setAttribute('width', wadah.scrollWidth);
  svg.setAttribute('height', wadah.scrollHeight);
  const titik = (id, sisi) => {
    const el = wadah.querySelector(`[data-simpul="${CSS.escape(id)}"]`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [(sisi === 'kanan' ? r.right : r.left) - kotak.left + wadah.scrollLeft, r.top + r.height / 2 - kotak.top + wadah.scrollTop];
  };
  const terang = S.lineage.terang || null;
  svg.innerHTML = S.lineage.edges.map((e) => {
    const a = titik(e.from, 'kanan');
    const b = titik(e.to, 'kiri');
    if (!a || !b) return '';
    const dx = Math.max(24, (b[0] - a[0]) / 2);
    const kelas = [LEMAH.has(e.kind) ? 'lemah' : '', terang ? (terang.has(`${e.from}>${e.to}`) ? 'terang' : 'pudar') : ''].join(' ');
    return `<path class="${kelas}" d="M${a[0]},${a[1]} C${a[0] + dx},${a[1]} ${b[0] - dx},${b[1]} ${b[0]},${b[1]}"><title>${esc(PERAN[e.kind] || e.kind)}</title></path>`;
  }).join('');
}

function sorotLineage(id) {
  const L = S.lineage;
  L.terpilih = id;
  const kena = id ? L.jalur.filter((p) => p.nodes.includes(id)) : [];
  L.terang = id ? new Set(kena.flatMap((p) => p.nodes.slice(1).map((n, i) => `${p.nodes[i]}>${n}`))) : null;
  const simpulTerang = new Set(kena.flatMap((p) => p.nodes));
  $('#lineage').querySelectorAll('[data-simpul]').forEach((b) => {
    b.classList.toggle('terang', simpulTerang.has(b.dataset.simpul));
    b.classList.toggle('pudar', Boolean(id) && !simpulTerang.has(b.dataset.simpul));
    b.setAttribute('aria-pressed', b.dataset.simpul === id ? 'true' : 'false');
  });
  garisLineage();
  const rincian = $('#lineage-rincian');
  if (!id) {
    rincian.innerHTML = '<p class="redup">Pilih simpul pada lineage untuk melihat jalur yang melewatinya.</p>';
    return;
  }
  rincian.innerHTML = `<h3>${esc(kena.length)} jalur melewati ${esc(L.simpul[id].label)}</h3><div class="gulir"><table>
    <thead><tr><th>Jalur</th><th>Peran di mapping</th><th>Tepi terlemah</th></tr></thead><tbody>
    ${kena.map((p) => `<tr><td>${p.nodes.map((n) => esc(L.simpul[n].label)).join(' <span class="redup">⊳</span> ')}</td>
      <td>${p.role ? esc(PERAN[p.role] || p.role) : '<span class="redup">tidak dipetakan</span>'}</td>
      <td>${p.weakest_link ? `<span class="${LEMAH.has(p.weakest_link) ? 'tanda peringatan' : ''}">${esc(PERAN[p.weakest_link] || p.weakest_link)}</span>` : '-'}</td></tr>`).join('')}
    </tbody></table></div>`;
}

/* ── halaman: perubahan skema ──────────────────────────────────────────────── */
async function halamanPerubahan() {
  const daftar = await k(`obdf/${S.oid}/events?limit=100`);
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Perubahan skema</h1>
      <p class="redup">Setiap DDL yang ditangkap monitor pada sumber, beserta hasil analisisnya. Perubahan pada tabel yang tidak difederasikan dicatat sebagai diabaikan.</p></div></div>
    <div class="panel gulir">${daftar.length ? `<table><thead><tr><th>Diterima</th><th>Perubahan</th><th>Status</th><th>Keterangan</th></tr></thead><tbody>
      ${daftar.map((e) => `<tr><td class="kecil">${esc(waktu(e.received_at))}</td><td>${esc(kalimatEvent(e.structured))}</td>
        <td>${tanda(e.status)}</td><td class="kecil redup">${esc(e.ignore_reason || '')}</td></tr>`).join('')}</tbody></table>`
      : '<p class="kosong">Belum ada perubahan skema. Monitor akan mencatat setiap ALTER TABLE pada sumber yang terdaftar.</p>'}</div>`;
}

/* ── halaman: rencana dan eksekusi ─────────────────────────────────────────── */
async function halamanAdaptasi() {
  await Promise.all([muatEvents(), muatTipe()]);
  const [rencana, eksekusi] = await Promise.all([k(`obdf/${S.oid}/plans?limit=60`), k(`obdf/${S.oid}/executions?limit=60`)]);
  const perRencana = {};
  for (const e of eksekusi) (perRencana[e.plan_id] = perRencana[e.plan_id] || []).push(e);
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Rencana dan eksekusi</h1>
      <p class="redup">Rencana adaptasi yang disusun dari setiap perubahan, dan jejak penerapannya. Pilih baris untuk melihat rinciannya.</p></div></div>
    <div class="panel gulir">${rencana.length ? `<table><thead><tr><th>Rencana</th><th>Perubahan</th><th>Pola</th><th>Keputusan</th><th>Status</th><th>Eksekusi</th></tr></thead><tbody>
      ${rencana.map((p) => { const e = (perRencana[p.id] || [])[0]; return `<tr class="dapat-dipilih" data-rencana="${esc(p.id)}" tabindex="0">
        <td>${esc(p.id)}</td><td>${esc(kalimatEvent((S.events[p.event_id] || {}).structured))}</td><td class="waktu">${esc(p.pattern || '-')}</td>
        <td>${tanda(p.decision)}</td><td>${tanda(p.status)}</td><td>${e ? `${tanda(e.status)} <span class="kecil redup">${esc(durasi(e.timings && e.timings.total_ms))}</span>` : '-'}</td></tr>`; }).join('')}
      </tbody></table>` : '<p class="kosong">Belum ada rencana adaptasi.</p>'}</div>
    <div id="rincian-rencana"></div>`;
  isi().querySelectorAll('[data-rencana]').forEach((tr) => {
    const buka = () => rincianRencana(rencana.find((p) => String(p.id) === tr.dataset.rencana), perRencana[tr.dataset.rencana] || []);
    tr.addEventListener('click', buka);
    tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') buka(); });
  });
}

function rincianRencana(plan, daftarEksekusi) {
  const wadah = $('#rincian-rencana');
  wadah.innerHTML = `<div style="margin-top:1.25rem">${dokumenUsulan(plan, { dapatDiputuskan: false })}</div>
    ${daftarEksekusi.map((e) => `<div class="panel"><h3>Eksekusi ${esc(e.id)}: ${(STATUS[e.status] || [e.status])[0]}</h3>
      <ul class="garis-waktu">${e.steps.map((s) => `<li><span>${esc(NAMA_LANGKAH[s.name] || s.name)}</span><span>${tanda(s.status)}</span>
        <span class="kecil redup">${esc(durasi(s.detail && s.detail.duration_ms))}${s.detail && s.detail.error ? `; ${esc(s.detail.error)}` : ''}${s.detail && s.detail.statements ? `; ${esc(s.detail.statements.join(' '))}` : ''}</span></li>`).join('')}</ul>
      ${e.failure ? `<p class="kecil" style="margin-top:0.75rem;color:var(--gagal)">${esc(e.failure.message || JSON.stringify(e.failure))}</p>` : ''}
      ${e.timings && e.timings.total_ms ? `<p class="kecil redup" style="margin-top:0.5rem">Total ${esc(durasi(e.timings.total_ms))}</p>` : ''}</div>`).join('')}`;
  wadah.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
}

/* ── halaman: versi spesifikasi dan perbandingan artefak ───────────────────── */
async function halamanVersi() {
  const versi = await k(`obdf/${S.oid}/versions?limit=60`);
  const pilihan = versi.map((v) => `<option value="${esc(v.id)}">Versi ${esc(v.version_no)} (${esc((STATUS[v.status] || [v.status])[0])})</option>`).join('');
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Versi spesifikasi</h1>
      <p class="redup">Setiap versi adalah potret lengkap OBDF: skema VDB, mapping, dan ontologi. Bandingkan dua versi untuk melihat apa yang diubah adaptasi.</p></div></div>
    <div class="panel"><h3>Bandingkan artefak</h3>
      <div class="baris-formulir">
        <div><label for="dari">Dari</label><select id="dari">${pilihan}</select></div>
        <div><label for="ke">Ke</label><select id="ke">${pilihan}</select></div>
        <div><label for="jenis">Artefak</label><select id="jenis">
          <option value="r2rml">Mapping (ℳ)</option><option value="ontology">Ontologi (𝒯)</option><option value="vdb_xml">VDB (Σ_S)</option></select></div>
      </div>
      <button class="utama-aksi" id="tombol-banding" type="button">Bandingkan</button>
      <div id="hasil-banding" style="margin-top:1rem"></div></div>
    <div class="panel gulir"><h3>Riwayat versi</h3><table><thead><tr><th>Versi</th><th>Status</th><th>Asal</th><th>VDB</th><th>Dibuat</th><th>Sidik jari</th></tr></thead><tbody>
      ${versi.map((v) => `<tr><td>${esc(v.version_no)}</td><td>${tanda(v.status)}</td><td>${esc(v.origin)}</td>
        <td>${esc(v.teiid_vdb_name)} v${esc(v.teiid_vdb_version)}</td><td class="kecil">${esc(waktu(v.created_at))}</td>
        <td><code>${esc((v.content_digest || '').slice(0, 12))}</code></td></tr>`).join('')}</tbody></table></div>`;
  if (versi.length > 1) { $('#dari').selectedIndex = 1; $('#ke').selectedIndex = 0; }
  $('#tombol-banding').addEventListener('click', bandingkan);
}

async function bandingkan() {
  const wadah = $('#hasil-banding');
  wadah.innerHTML = '<p class="redup">Membandingkan artefak</p>';
  try {
    const d = await api(`/api/ui/diff?dari=${encodeURIComponent($('#dari').value)}&ke=${encodeURIComponent($('#ke').value)}&kind=${encodeURIComponent($('#jenis').value)}`);
    if (d.identik) { wadah.innerHTML = '<p class="redup">Artefak identik pada kedua versi.</p>'; return; }
    wadah.innerHTML = `<p class="kecil redup" style="margin-bottom:0.5rem">${esc(d.tambah)} baris ditambah, ${esc(d.hapus)} baris dihapus</p>
      <div class="diff">${d.diff.map((b) => {
        const kelas = b.startsWith('+++') || b.startsWith('---') ? 'kepala' : b.startsWith('@@') ? 'blok' : b.startsWith('+') ? 'tambah' : b.startsWith('-') ? 'hapus' : '';
        return `<div class="${kelas}">${esc(b) || ' '}</div>`;
      }).join('')}</div>`;
  } catch (e) { wadah.innerHTML = `<p style="color:var(--gagal)">${esc(e.message)}</p>`; }
}

/* ── halaman: analisis dampak (pratinjau tanpa mengubah apa pun) ───────────── */
async function halamanDampak() {
  await muatTipe();
  const sumber = await k(`obdf/${S.oid}/sources`);
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Analisis dampak</h1>
      <p class="redup">Simulasikan perubahan skema sebelum benar-benar terjadi. ASCAM menghitung kolom yang terdampak dan menyusun rencana, tanpa mengubah apa pun.</p></div></div>
    <form class="panel" id="formulir-dampak">
      <div class="baris-formulir">
        <div><label for="d-op">Perubahan</label><select id="d-op"><option value="add">Tambah kolom</option><option value="drop">Hapus kolom</option><option value="rename">Ganti nama kolom</option></select></div>
        <div><label for="d-sumber">Sumber</label><select id="d-sumber">${sumber.map((s) => `<option value="${esc(s.logical_name)}">${esc(s.logical_name)} (${esc(s.dbms)})</option>`).join('')}</select></div>
        <div><label for="d-tabel">Tabel</label><input id="d-tabel" required placeholder="penerima_manfaat"></div>
        <div><label for="d-kolom">Kolom di sumber</label><input id="d-kolom" required placeholder="email"></div>
        <div><label for="d-baru">Nama baru (ganti nama)</label><input id="d-baru"></div>
        <div><label for="d-tipe">Tipe (tambah kolom)</label><input id="d-tipe" placeholder="varchar(100)"></div>
      </div>
      <button class="utama-aksi" type="submit">Hitung dampak</button>
    </form>
    <div id="hasil-dampak"></div>`;
  $('#formulir-dampak').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const body = { operation: $('#d-op').value, source: $('#d-sumber').value, table: $('#d-tabel').value.trim(),
      column: $('#d-kolom').value.trim(), new_column: $('#d-baru').value.trim() || null, column_type: $('#d-tipe').value.trim() || null };
    try {
      const r = await k(`obdf/${S.oid}/impact`, { method: 'POST', body });
      const semu = { id: '-', status: r.decision, pattern: r.pattern, created_at: new Date().toISOString(), base_spec_version_id: r.spec_version_id,
        reasons: r.reasons, impact: r, actions: r.actions.map((a, i) => ({ seq: i + 1, artifact: a.artifact, operation: a.operation,
          params: Object.fromEntries(Object.entries(a).filter(([kunci]) => !['artifact', 'operation'].includes(kunci))) })) };
      S.events['-'] = { structured: { ...body, schema: null } };
      semu.event_id = '-';
      $('#hasil-dampak').innerHTML = `<div style="margin-top:1.25rem">${dokumenUsulan(semu, { dapatDiputuskan: false, pratinjau: true })}</div>`;
    } catch (e) { kabar(e.message, 'gagal'); }
  });
}

/* ── halaman: konfigurasi ──────────────────────────────────────────────────── */
async function halamanKonfigurasi() {
  const [sumber, target, pengaturan, penamaan] = await Promise.all([
    k(`obdf/${S.oid}/sources`), k(`obdf/${S.oid}/targets`), k(`obdf/${S.oid}/settings`), k(`obdf/${S.oid}/naming-policy`)]);
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Konfigurasi</h1>
      <p class="redup">Sumber yang dipantau, komponen OBDF yang dikelola ASCAM, dan aturan keputusan adaptasi.</p></div></div>
    <div class="panel"><h3>Kebijakan adaptasi</h3>
      <table><tbody>
        <tr><td>${tanda('auto')}</td><td>Perubahan yang dampaknya tertelusuri penuh, ditentukan oleh DDL, dan tidak mengubah identitas, baris, atau nilai elemen lain. Diterapkan langsung.</td></tr>
        <tr><td>${tanda('hitl')}</td><td>Penambahan kolom. Nama dan domain property baru adalah keputusan pemodelan, sehingga usulan ASCAM menunggu persetujuan Anda dan dapat direvisi.</td></tr>
        <tr><td>${tanda('notify')}</td><td>Penghapusan atau penggantian nama kolom yang tidak terbukti aman. ASCAM tidak mengubah OBDF; elemen yang terdampak dilaporkan di halaman Notifikasi.</td></tr>
      </tbody></table></div>
    <div class="grid-dua">
      <div class="panel gulir"><h3>Sumber data</h3><table><thead><tr><th>Nama</th><th>DBMS</th><th>Skema bawaan</th><th>Topik monitor</th></tr></thead><tbody>
        ${sumber.map((s) => `<tr><td>${esc(s.logical_name)}</td><td>${esc(s.dbms)}</td><td>${esc(s.default_schema)}</td><td class="kecil pecah"><code>${esc(s.kafka_topic)}</code></td></tr>`).join('')}</tbody></table></div>
      <div class="panel"><h3>Kebijakan penamaan property</h3><table><tbody>
        <tr><td class="redup">Namespace</td><td><code>${esc((pengaturan.find((p) => p.key === 'ontology.namespace') || {}).value || '-')}</code></td></tr>
        <tr><td class="redup">Templat IRI</td><td><code>${esc(penamaan.property_iri_template)}</code></td></tr>
        <tr><td class="redup">Bila nama bentrok</td><td>${esc(BENTROK[penamaan.on_collision] || penamaan.on_collision)}</td></tr></tbody></table></div>
    </div>
    <div class="panel gulir"><h3>Komponen OBDF</h3><table><thead><tr><th>Jenis</th><th>Alamat</th><th>Aktif</th><th></th></tr></thead><tbody>
      ${target.map((t) => `<tr><td>${esc(JENIS_TARGET[t.kind] || t.kind)}</td><td class="kecil"><code>${esc(t.endpoint.host)}:${esc(t.endpoint.port)}${esc(t.endpoint.path || '')}</code></td>
        <td>${t.enabled ? 'ya' : 'tidak'}</td><td><button class="kecil" type="button" data-periksa="${esc(t.id)}">Periksa</button>
        <span class="kecil" id="periksa-${esc(t.id)}"></span></td></tr>`).join('')}</tbody></table></div>`;
  isi().querySelectorAll('[data-periksa]').forEach((b) => b.addEventListener('click', async () => {
    const id = b.dataset.periksa;
    $(`#periksa-${id}`).textContent = ' memeriksa';
    try {
      const h = await k(`targets/${id}/check`, { method: 'POST', body: {} });
      $(`#periksa-${id}`).innerHTML = ` ${h.ok ? '<span class="tanda berhasil">Terhubung</span>' : '<span class="tanda gagal">Gagal</span>'} ${esc(h.latency_ms)} ms`;
    } catch (e) { $(`#periksa-${id}`).textContent = ` ${e.message}`; }
  }));
}

/* ── halaman: jejak audit ──────────────────────────────────────────────────── */
async function halamanAudit() {
  const daftar = await k(`obdf/${S.oid}/audit`);
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Jejak audit</h1>
      <p class="redup">Setiap tindakan pada Knowledge, termasuk keputusan administrator, sinkronisasi, dan eksekusi, dengan pelakunya.</p></div></div>
    <div class="panel gulir"><table><thead><tr><th>Waktu</th><th>Pelaku</th><th>Tindakan</th><th>Objek</th><th>Rincian</th></tr></thead><tbody>
      ${daftar.map((a) => `<tr><td class="kecil">${esc(waktu(a.created_at || a.at))}</td><td>${esc(a.actor)}</td><td>${esc(a.action)}</td>
        <td class="kecil">${esc(a.object_kind || '')} ${esc(a.object_ref || '')}</td>
        <td class="kecil redup"><code>${esc(JSON.stringify(a.detail || {}).slice(0, 160))}</code></td></tr>`).join('')}</tbody></table></div>`;
}

/* ── kerangka, rute, dan pembaruan berkala ─────────────────────────────────── */
const RUTE = {
  ringkasan: halamanRingkasan, persetujuan: halamanPersetujuan, perubahan: halamanPerubahan, adaptasi: halamanAdaptasi,
  versi: halamanVersi, dampak: halamanDampak, konfigurasi: halamanKonfigurasi, audit: halamanAudit,
  notifikasi: halamanNotifikasi, lineage: halamanLineage,
};

async function tampilkanRute() {
  const nama = ruteSekarang();
  const halaman = RUTE[nama] || halamanRingkasan;
  document.querySelectorAll('.navigasi a[data-rute]').forEach((a) => {
    if (a.dataset.rute === nama) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  clearInterval(S.pemutar);
  try {
    await halaman();
    if (nama === 'ringkasan' || !RUTE[nama]) S.pemutar = setInterval(() => halamanRingkasan().catch(() => {}), 5000);
  } catch (e) {
    isi().innerHTML = `<div class="panel"><h3>Halaman tidak dapat dimuat</h3><p style="color:var(--gagal)">${esc(e.message)}</p>
      <p class="redup" style="margin-top:0.5rem">Periksa apakah Knowledge Service berjalan, lalu muat ulang halaman.</p></div>`;
  }
}

async function segarkanLencana() {
  try {
    const [r, sehat] = await Promise.all([api('/api/ui/overview'), api('/api/ui/health')]);
    const jumlah = r.pending.length;
    const lencana = $('#jumlah-menunggu');
    lencana.textContent = jumlah;
    lencana.hidden = jumlah === 0;
    // hanya memberi kabar bila ada usulan atau notifikasi BARU sejak pemeriksaan sebelumnya
    if (S.menunggu !== null && jumlah > S.menunggu) kabar(`${jumlah} usulan penambahan kolom menunggu persetujuan Anda.`, 'keputusan', ['#/persetujuan', 'Tinjau']);
    S.menunggu = jumlah;
    const pemberitahuan = r.notifications.filter((n) => !r.pending.some((p) => p.id === n.plan_id)).length;
    const lencanaN = $('#jumlah-notifikasi');
    lencanaN.textContent = pemberitahuan;
    lencanaN.hidden = pemberitahuan === 0;
    if (S.belumDibaca !== null && pemberitahuan > S.belumDibaca) kabar('Ada perubahan skema yang tidak dapat diadaptasi otomatis.', 'peringatan', ['#/notifikasi', 'Lihat']);
    S.belumDibaca = pemberitahuan;
    const v = r.active_version || {};
    $('#versi-aktif').textContent = v.version_no ? `Versi spesifikasi ${v.version_no}, VDB v${v.teiid_vdb_version}` : 'Belum ada versi aktif';
    const exe = sehat.executor || {};
    const tombol = $('#tombol-executor');
    if (exe.state) {
      $('#status-executor').textContent = exe.paused ? 'Executor dijeda' : `Executor ${exe.state === 'running' ? 'berjalan' : exe.state}`;
      tombol.hidden = false;
      tombol.textContent = exe.paused ? 'Lanjutkan executor' : 'Jeda executor';
      tombol.dataset.aksi = exe.paused ? 'resume' : 'pause';
    } else {
      $('#status-executor').textContent = 'Executor tidak menjawab';
      tombol.hidden = true;
    }
  } catch { /* abaikan; akan dicoba lagi */ }
}

function tampilkanMasuk() {
  $('#konsol').hidden = true;
  $('#layar-masuk').hidden = false;
  clearInterval(S.pemutar);
  $('#nama-pengguna').focus();
}

async function mulaiKonsol(me) {
  S.user = me.user;
  const daftar = await k('obdf');
  S.obdf = daftar.find((o) => o.name === me.obdf) || daftar[0];
  if (!S.obdf) throw new Error('Belum ada OBDF yang terdaftar di Knowledge.');
  S.oid = S.obdf.id;
  $('#nama-admin').textContent = S.user;
  $('#nama-obdf').textContent = S.obdf.name;
  $('#layar-masuk').hidden = true;
  $('#konsol').hidden = false;
  await segarkanLencana();
  await tampilkanRute();
}

document.addEventListener('DOMContentLoaded', async () => {
  $('#formulir-masuk').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    $('#pesan-masuk').textContent = '';
    try {
      await api('/api/ui/login', { method: 'POST', body: { username: $('#nama-pengguna').value, password: $('#kata-sandi').value } });
      $('#kata-sandi').value = '';
      await mulaiKonsol(await api('/api/ui/me'));
    } catch (e) { $('#pesan-masuk').textContent = e.message; }
  });
  $('#tombol-keluar').addEventListener('click', async () => {
    await api('/api/ui/logout', { method: 'POST' }).catch(() => {});
    tampilkanMasuk();
  });
  $('#tombol-executor').addEventListener('click', async (ev) => {
    const aksi = ev.currentTarget.dataset.aksi;
    try {
      await api(`/api/ui/executor/${aksi}`, { method: 'POST' });
      kabar(aksi === 'pause' ? 'Executor dijeda. Rencana baru tidak akan diterapkan sampai dilanjutkan.' : 'Executor dilanjutkan.');
      await segarkanLencana();
    } catch (e) { kabar(e.message, 'gagal'); }
  });
  window.addEventListener('hashchange', tampilkanRute);
  window.addEventListener('resize', () => { if ($('#lineage')) garisLineage(); });
  setInterval(segarkanLencana, 8000);
  try { await mulaiKonsol(await api('/api/ui/me')); } catch { tampilkanMasuk(); }
});
