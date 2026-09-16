"""
Simulasi offline fase Execute & Verify ASCAM (tanpa Docker).

Yang disimulasikan:
  - skema fisik sumber Σ_i (dict PHYS),
  - deployment scanner WildFly (marker .dodeploy/.isdeploying/.deployed/.failed),
  - endpoint Ontop yang memvalidasi mapping terhadap VDB aktif dan Σ_i,
  - restart kontainer Ontop (memuat ulang mapping).

Menjalankan:  python tests/sim_execute_verify.py   (dari folder adaptive-engine)
Catatan: ini pengujian logika Executor, BUKAN pengganti eksperimen pada stack asli.
"""
import os, sys, json, re, shutil, tempfile, threading, time, http.server, urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
SETUP = os.path.abspath(os.path.join(ENGINE, '..', '..'))
W = tempfile.mkdtemp(prefix='ascam-sim-')
for d in ('deploy', 'ontop', 'state'):
    os.makedirs(f'{W}/{d}')
shutil.copy(f'{SETUP}/data-federation/deployments/government-vdb.xml', f'{W}/deploy/')
shutil.copy(f'{SETUP}/vkg-system/config/mapping.obda', f'{W}/ontop/')
shutil.copy(f'{SETUP}/vkg-system/config/ontology_file.ttl', f'{W}/ontop/')
os.environ.update(VDB_PATH=f'{W}/deploy/government-vdb.xml', OBDA_PATH=f'{W}/ontop/mapping.obda',
                  TTL_PATH=f'{W}/ontop/ontology_file.ttl', ASCAM_STATE_DIR=f'{W}/state',
                  ONTOP_SPARQL_URL='http://127.0.0.1:18899/sparql', TEIID_SCAN_POLL='0.1')
sys.path.insert(0, ENGINE)

# --- skema fisik sumber (Σ_i) yang disimulasikan ------------------------
PHYS = {'program_bansos': {'program_id','nama_program','tipe_program','nominal','periode_mulai','periode_selesai'},
        'penerima_manfaat': {'penerima_id','nik','nama_lengkap','tgl_lahir','status_ekonomi','no_kartu_keluarga','aktif'},
        'master_penduduk': {'nik','no_kk','nama','tanggal_lahir','pekerjaan','penghasilan','status_hidup'}}
DEPLOYED_VDB = {}   # tabel -> kolom, yang benar2 aktif di "Teiid"
FAIL_DEPLOY = {'on': False}

def vdb_tables(text):
    out = {}
    for m in re.finditer(r'CREATE FOREIGN TABLE\s+(\w+)\s*\((.*?)\)\s*OPTIONS', text, re.S|re.I):
        out[m.group(1)] = {l.strip().split()[0] for l in m.group(2).split(',') if l.strip()}
    return out

# --- scanner WildFly palsu ------------------------------------------------
def scanner():
    vdb = f'{W}/deploy/government-vdb.xml'
    while True:
        if os.path.exists(vdb + '.dodeploy'):
            open(vdb + '.isdeploying','w').close()
            time.sleep(0.5)
            import xml.etree.ElementTree as ET
            try:
                ET.parse(vdb)
                if FAIL_DEPLOY['on']: raise RuntimeError('simulated failure')
                DEPLOYED_VDB.clear(); DEPLOYED_VDB.update(vdb_tables(open(vdb).read()))
                # tiru handleSuccessResult(): hapus .failed, tulis .deployed, mtime = mtime VDB
                if os.path.exists(vdb + '.failed'): os.remove(vdb + '.failed')
                open(vdb + '.deployed','w').write('government-vdb.xml')
                m = os.path.getmtime(vdb); os.utime(vdb + '.deployed', (m, m))
            except Exception as e:
                # tiru writeFailedMarker(): hapus .deployed, tulis .failed
                if os.path.exists(vdb + '.deployed'): os.remove(vdb + '.deployed')
                open(vdb + '.failed','w').write(str(e))
            os.remove(vdb + '.dodeploy'); os.remove(vdb + '.isdeploying')
        time.sleep(0.1)

# --- Ontop palsu: validasi mapping terhadap VDB aktif & skema fisik ---------
LOADED = {}
def load_ontop():
    txt = open(f'{W}/ontop/mapping.obda').read()
    LOADED.clear()
    for m in re.finditer(r'mappingId\s+(\S+)\s*\ntarget\s+(.*?)\s*\nsource\s+(.*?)\s*(?=\n\s*mappingId|\n\s*\]\])', txt, re.S):
        LOADED[m.group(1)] = (m.group(2), m.group(3))

def check_prop(local):
    hits = []
    for mid,(tgt,src) in LOADED.items():
        for pm in re.finditer(rf'bansos:{local}\s+\{{(\w+)\}}', tgt):
            var = pm.group(1)
            table = re.search(r'FROM\s+\w+\.(\w+)', src).group(1)
            if table not in PHYS: return 200, 5
            sel = re.match(r'SELECT\s+(.*?)\s+FROM', src, re.S|re.I).group(1)
            if sel.strip() == '*':
                phys = var
                if var not in DEPLOYED_VDB[table]: return 500, f'{var} tidak ada di VDB'
            else:
                items = {}
                for it in sel.split(','):
                    parts = re.split(r'\s+AS\s+', it.strip(), flags=re.I)
                    items[parts[-1]] = parts[0]
                for out, ph in items.items():   # semua kolom SELECT harus ada di VDB & fisik
                    if ph not in DEPLOYED_VDB[table] or ph not in PHYS[table]:
                        return 500, f'kolom {ph} tidak ada (VDB/fisik)'
                if var not in items: return 500, f'variabel {var} tidak di-SELECT'
                phys = items[var]
            if phys not in PHYS[table]: return 500, f'{phys} tidak ada di sumber'
            hits.append(5)
    return 200, sum(hits)

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)['query'][0]
        if q.startswith('ASK'): code, n = 200, 1
        elif ' a bansos:' in q: code, n = 200, 5
        else: code, n = check_prop(re.search(r'\?s bansos:(\w+) \?o', q).group(1))
        body = json.dumps({'boolean': True} if q.startswith('ASK') else {'results': {'bindings': [{'n': {'value': str(n)}}]}}) if code == 200 else str(n)
        self.send_response(code); self.end_headers(); self.wfile.write(body.encode())

threading.Thread(target=scanner, daemon=True).start()
srv = http.server.ThreadingHTTPServer(('127.0.0.1', 18899), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# --- docker palsu: restart = muat ulang mapping ----------------------------
import executor.ontop_controller as oc
class FakeC:
    def restart(self, timeout): load_ontop()
class FakeClient:
    class containers:
        @staticmethod
        def get(name): return FakeC()
oc.docker.from_env = lambda: FakeClient()

open(f'{W}/deploy/government-vdb.xml.dodeploy','w').close(); time.sleep(1); load_ontop()
import main

def ev(i, table, ddl, ts):
    return {'payload': {'op':'c','after': {'id': i, 'table_name': table, 'is_regulated': True,
            'ddl_command': ddl, 'captured_at': ts}}}

def run(title, i, table, ddl, ts, phys_change):
    phys_change()                        # DDL terjadi di sumber
    print(f'\n===== {title} =====')
    main.process_event(ev(i, table, ddl, ts), int(time.time()*1000))
    rec = [json.loads(l) for l in open(f'{W}/state/adaptation_log.jsonl')][-1]
    print('STATUS:', rec['status'], '| failure:', rec['failure'])
    for r in rec.get('verification',{}).get('results',[]): print('  check', r['name'], r['passed'], r['value'], r['detail'])
    print('  durations:', rec['durations'])
    return rec

iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
ms  = int(time.time()*1000)
# P-001 pada mapping dengan SELECT eksplisit (bug lama)
run('P-001 ADD kemensos.program_bansos.kuota', 1, 'program_bansos',
    'ALTER TABLE public.program_bansos ADD COLUMN kuota integer', iso,
    lambda: PHYS['program_bansos'].add('kuota'))
# P-002 pada kolom yang ada di SELECT eksplisit (bug lama)
run('P-002 DROP kemensos.program_bansos.tipe_program', 2, 'program_bansos',
    'ALTER TABLE public.program_bansos DROP COLUMN tipe_program', iso,
    lambda: PHYS['program_bansos'].discard('tipe_program'))
# P-003 pada SELECT * (MySQL, captured_at epoch ms)
run('P-003 RENAME dukcapil.master_penduduk.tanggal_lahir -> tgl_lahir_ktp', 3, 'master_penduduk',
    'ALTER TABLE `dukcapil`.`master_penduduk` RENAME COLUMN `tanggal_lahir` TO `tgl_lahir_ktp`', ms,
    lambda: (PHYS['master_penduduk'].discard('tanggal_lahir'), PHYS['master_penduduk'].add('tgl_lahir_ktp')))
# Skenario gagal: deploy VDB gagal -> revert
before = open(f'{W}/ontop/mapping.obda').read()
FAIL_DEPLOY['on'] = True
run('GAGAL: ADD kemensos.penerima_manfaat.email (deploy gagal)', 4, 'penerima_manfaat',
    'ALTER TABLE public.penerima_manfaat ADD COLUMN email varchar(100)', iso,
    lambda: PHYS['penerima_manfaat'].add('email'))
FAIL_DEPLOY['on'] = False
print('mapping direvert:', open(f'{W}/ontop/mapping.obda').read() == before)
print('email tidak ada di VDB aktif:', 'email' not in DEPLOYED_VDB['penerima_manfaat'])
print('\nMAP-PROGRAM akhir:', LOADED['MAP-PROGRAM'][1])
print('MAP-PENDUDUK akhir:', LOADED['MAP-PENDUDUK'][1])
print('isi folder deploy:', sorted(os.listdir(f'{W}/deploy')))
srv.shutdown()
print('\nDirektori kerja simulasi:', W)
