'use strict';
/* Konsol administrator ASCAM.
   Seluruh data dinamis melewati esc() sebelum masuk ke HTML, karena isi event, rencana, dan
   artefak berasal dari sistem lain dan tidak boleh dipercaya sebagai markup. */

const S = { user: null, obdf: null, oid: null, menunggu: null, tipe: {}, events: {}, pemutar: null };
const LANGKAH = ['deploy_vdb', 'validate', 'switch', 'reload_ontop', 'verify', 'sync', 'rollback'];
const NAMA_LANGKAH = {
  deploy_vdb: 'Deploy VDB', validate: 'Validasi', switch: 'Pindah koneksi', reload_ontop: 'Muat ulang Ontop',
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

async function halamanRingkasan() {
  const [r, sehat] = await Promise.all([api('/api/ui/overview'), api('/api/ui/health')]);
  const ev = r.events[0];
  const eks = r.executions[0];
  const exe = sehat.executor || {};
  const versi = r.active_version || {};
  const tahap = [
    { nama: 'Monitor', nilai: r.events.length ? relatif(ev.received_at) : '-',
      ket: ev ? kalimatEvent(ev.structured) : 'Belum ada perubahan skema', tautan: '#/perubahan' },
    { nama: 'Analisis', nilai: ev ? (STATUS[ev.status] || [ev.status])[0] : '-',
      ket: ev && ev.ignore_reason ? ev.ignore_reason : 'Dampak dihitung dari lineage kolom', tautan: '#/adaptasi' },
    { nama: 'Keputusan', nilai: `${r.pending.length} menunggu`, perhatian: r.pending.length > 0,
      ket: r.pending.length ? 'Usulan perubahan menunggu persetujuan Anda' : 'Tidak ada usulan yang menunggu', tautan: '#/persetujuan' },
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

/* ── halaman: persetujuan (HITL) ───────────────────────────────────────────── */
function dokumenUsulan(plan, { dapatDiputuskan, pratinjau = false }) {
  const ev = S.events[plan.event_id];
  const struktur = ev ? ev.structured : null;
  return `<article class="usulan" id="usulan-${esc(plan.id)}">
    <header><h2>${esc(kalimatEvent(struktur))}</h2>${tanda(plan.status)}
      <span class="kecil redup">${pratinjau ? `Pola ${esc(plan.pattern || '-')}, dihitung terhadap versi spesifikasi ${esc(plan.base_spec_version_id)}`
        : `Rencana ${esc(plan.id)}, pola ${esc(plan.pattern || '-')}, disusun ${esc(relatif(plan.created_at))} di atas versi spesifikasi ${esc(plan.base_spec_version_id)}`}</span></header>
    <section><h3>${['executed', 'failed'].includes(plan.status) ? 'Yang dilakukan ASCAM' : 'Yang akan dilakukan ASCAM'}</h3>${daftarTindakan(plan)}</section>
    ${dapatDiputuskan ? `<section><h3>Mengapa butuh keputusan Anda</h3>${daftarAlasan(plan.reasons)}</section>`
      : plan.reasons && plan.reasons.length ? `<section><h3>Catatan analisis</h3>${daftarAlasan(plan.reasons)}</section>` : ''}
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
  const diputuskan = semua.filter((p) => p.decided_by && p.decision === 'hitl').slice(0, 8);
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Persetujuan</h1>
      <p class="redup">Usulan adaptasi yang tidak dijalankan otomatis. Periksa apa yang akan diubah pada setiap lapisan OBDF, lalu setujui atau tolak. Keputusan dicatat atas nama Anda.</p></div></div>
    ${menunggu.length ? menunggu.map((p) => dokumenUsulan(p, { dapatDiputuskan: true })).join('')
      : '<div class="panel"><p class="kosong">Tidak ada usulan yang menunggu keputusan. Perubahan yang memerlukan persetujuan, seperti penambahan kolom, akan muncul di sini.</p></div>'}
    ${diputuskan.length ? `<div class="panel" style="margin-top:1.25rem"><h3>Keputusan terakhir</h3><table>
      <thead><tr><th>Rencana</th><th>Perubahan</th><th>Keputusan</th><th>Oleh</th><th>Waktu</th></tr></thead><tbody>
      ${diputuskan.map((p) => `<tr><td>${esc(p.id)}</td><td>${esc(kalimatEvent((S.events[p.event_id] || {}).structured))}</td>
        <td>${tanda(p.status)}</td><td>${esc(p.decided_by)}</td><td>${esc(waktu(p.decided_at))}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
  isi().querySelectorAll('[data-putuskan]').forEach((b) => b.addEventListener('click', putuskan));
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
  isi().querySelectorAll(`[data-plan="${id}"]`).forEach((b) => { b.disabled = true; });
  try {
    await k(`plans/${id}/${aksi}`, { method: 'POST', body: { note: catatan || null } });
    kabar(aksi === 'approve' ? 'Disetujui. Executor akan menerapkan rencana ini dalam beberapa detik.' : 'Ditolak. Rencana tidak akan diterapkan.',
      aksi === 'approve' ? 'keputusan' : '', aksi === 'approve' ? ['#/adaptasi', 'Pantau eksekusi'] : null);
    await segarkanLencana();
    await halamanPersetujuan();
  } catch (e) {
    kabar(e.message, 'gagal');
    isi().querySelectorAll(`[data-plan="${id}"]`).forEach((b) => { b.disabled = false; });
  }
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
  const add = (pengaturan.find((p) => p.key === 'adaptation.add_column') || { value: { mode: 'auto' } }).value.mode;
  isi().innerHTML = `
    <div class="kepala-halaman"><div><h1>Konfigurasi</h1>
      <p class="redup">Sumber yang dipantau, komponen OBDF yang dikelola ASCAM, dan kebijakan adaptasi.</p></div></div>
    <form class="panel" id="formulir-kebijakan"><h3>Kebijakan penambahan kolom</h3>
      <p class="redup" style="margin-bottom:1rem">Penambahan kolom menambah kosakata baru ke ontologi (IRI, domain, range). Pilih apakah ASCAM menerapkannya langsung atau menunggu persetujuan Anda.</p>
      <div class="pilihan">
        <label><input type="radio" name="mode-add" value="hitl" ${add === 'hitl' ? 'checked' : ''}><span><strong>Perlu persetujuan</strong><br><span class="redup kecil">Usulan muncul di halaman Persetujuan dan baru diterapkan setelah disetujui.</span></span></label>
        <label><input type="radio" name="mode-add" value="auto" ${add === 'auto' ? 'checked' : ''}><span><strong>Otomatis</strong><br><span class="redup kecil">Diterapkan langsung bila tidak ada bentrok nama atau domain.</span></span></label>
      </div>
      <p style="margin-top:1rem"><button class="utama-aksi" type="submit">Simpan kebijakan</button></p></form>
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
  $('#formulir-kebijakan').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const mode = isi().querySelector('input[name="mode-add"]:checked').value;
    try {
      await k(`obdf/${S.oid}/settings/adaptation.add_column`, { method: 'PUT', body: { value: { mode } } });
      kabar(mode === 'hitl' ? 'Disimpan. Penambahan kolom kini menunggu persetujuan.' : 'Disimpan. Penambahan kolom kini diterapkan otomatis.');
    } catch (e) { kabar(e.message, 'gagal'); }
  });
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
};

async function tampilkanRute() {
  const nama = (location.hash.replace(/^#\//, '') || 'ringkasan').split('?')[0];
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
    // hanya memberi kabar bila ada usulan BARU sejak pemeriksaan sebelumnya
    if (S.menunggu !== null && jumlah > S.menunggu) kabar(`${jumlah} usulan perubahan menunggu persetujuan Anda.`, 'keputusan', ['#/persetujuan', 'Tinjau']);
    S.menunggu = jumlah;
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
  setInterval(segarkanLencana, 8000);
  try { await mulaiKonsol(await api('/api/ui/me')); } catch { tampilkanMasuk(); }
});
