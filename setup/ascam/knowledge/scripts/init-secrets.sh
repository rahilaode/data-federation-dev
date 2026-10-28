#!/usr/bin/env bash
# Creates the Knowledge secret files (once; existing files are not overwritten).
# The files are not committed (secrets/.gitignore).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)/secrets"
mkdir -p "$DIR"
chmod 700 "$DIR"

write() {  # $1 file name, $2 content
  if [ ! -s "$DIR/$1" ]; then
    printf '%s\n' "$2" > "$DIR/$1"
    chmod 644 "$DIR/$1"      # read by a non-root process inside the container; the folder stays 700
    echo "created: secrets/$1"
  fi
}
rand() { python3 -c "import secrets; print(secrets.token_urlsafe(32))"; }
fernet_key() { python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"; }

# basis data Knowledge
write knowledge_db_owner_password "$(rand)"
write knowledge_db_app_password "$(rand)"
# credential encryption: one key per line, newest key on the first line
write knowledge_encryption_keys "$(fernet_key)"
# service-to-service API tokens: <client>:<token>
write knowledge_api_tokens "$(printf 'ui:%s\norchestrator:%s\nexecutor:%s' "$(rand)" "$(rand)" "$(rand)")"

# Ontop agent token: Knowledge stores the raw token as a credential, while the agent
# reads a file in the format <client>:<token>.
if [ ! -s "$DIR/obdf_ontop_agent_token" ]; then
  AGENT_TOKEN="$(rand)"
  write obdf_ontop_agent_token "$AGENT_TOKEN"
  write ontop_agent_api_tokens "knowledge:$AGENT_TOKEN"
fi

# LABORATORY OBDF credentials (default values of setup/data-federation/Dockerfile).
# In a real environment, put the credentials of the OBDF operator into these files.
write obdf_teiid_mgmt_password "Password12345_"
write obdf_teiid_user_password "Password12345_"

# ASCAM administrator console (setup/ascam/ui): admin password and session signing key.
# The password is shown once when it is created; keep it in your password manager.
if [ ! -s "$DIR/ui_admin_password" ]; then
  UI_PASSWORD="$(rand)"
  write ui_admin_password "$UI_PASSWORD"
  echo "console password (user admin): $UI_PASSWORD"
fi
write ui_session_key "$(rand)"
