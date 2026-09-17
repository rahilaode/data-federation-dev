#!/usr/bin/env bash
# Membuat berkas rahasia Knowledge (sekali saja; berkas yang sudah ada tidak ditimpa).
# Berkas tidak di-commit (secrets/.gitignore).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)/secrets"
mkdir -p "$DIR"
chmod 700 "$DIR"

write() {  # $1 nama berkas, $2 isi
  if [ ! -s "$DIR/$1" ]; then
    printf '%s\n' "$2" > "$DIR/$1"
    chmod 644 "$DIR/$1"      # dibaca proses non-root di dalam kontainer; folder tetap 700
    echo "dibuat: secrets/$1"
  fi
}
rand() { python3 -c "import secrets; print(secrets.token_urlsafe(32))"; }
fernet_key() { python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"; }

# basis data Knowledge
write knowledge_db_owner_password "$(rand)"
write knowledge_db_app_password "$(rand)"
# enkripsi kredensial: satu kunci per baris, kunci terbaru di baris pertama
write knowledge_encryption_keys "$(fernet_key)"
# token API antarlayanan: <klien>:<token>
write knowledge_api_tokens "$(printf 'ui:%s\norchestrator:%s\nexecutor:%s' "$(rand)" "$(rand)" "$(rand)")"

# Kredensial OBDF LABORATORIUM (nilai bawaan setup/data-federation/Dockerfile).
# Pada lingkungan nyata, isi berkas ini dengan kredensial milik pengelola OBDF.
write obdf_teiid_mgmt_password "Password12345_"
write obdf_teiid_user_password "Password12345_"
