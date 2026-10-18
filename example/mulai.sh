#!/usr/bin/env bash
# mulai.sh — menyalakan contoh multi-source model (dari folder example/).
#
# Folder teiid/deployments di-mount ke WildFly, yang berjalan sebagai pengguna `jboss`
# di dalam kontainer. Deployment scanner harus dapat menulis berkas marker (.deployed,
# .failed) di folder itu; tanpa izin tulis, VDB tidak pernah di-deploy (WFLYDS0043).
# Ini sama dengan langkah chmod pada start.sh untuk setup/data-federation/deployments.
set -euo pipefail
cd "$(dirname "$0")"
chmod 777 teiid/deployments
docker-compose up -d --build
echo "Kontainer menyala. Jalankan ./uji.sh untuk menguji."
