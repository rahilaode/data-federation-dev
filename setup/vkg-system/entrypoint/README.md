# Entrypoint Ontop 4.1.1 dengan `exec java`

`entrypoint-4.1.1-exec.sh` adalah salinan `/opt/ontop/entrypoint.sh` dari image
`ontop/ontop-endpoint:4.1.1` dengan **satu** perubahan pada baris yang menjalankan JVM:

```diff
-java ${ONTOP_JAVA_ARGS} -cp ...
+exec java ${ONTOP_JAVA_ARGS} -cp ...
```

Tanpa `exec`, bash tetap menjadi induk proses Java dan tidak meneruskan SIGTERM,
sehingga `docker stop`/`docker restart` selalu menunggu batas waktu (10 s) lalu
mengirim SIGKILL. Ontop 5.0.0 sudah memakai `exec java` pada entrypoint resminya;
berkas ini adalah backport perilaku tersebut ke 4.1.1.

Membuat ulang berkas (reprodusibel, diambil dari image yang dipakai):

```bash
docker run --rm --entrypoint cat ontop/ontop-endpoint:4.1.1 /opt/ontop/entrypoint.sh > /tmp/entrypoint-upstream.sh
python3 - <<'PY'
src = open('/tmp/entrypoint-upstream.sh').read()
old = '\njava ${ONTOP_JAVA_ARGS}'
assert src.count(old) == 1
open('setup/vkg-system/entrypoint/entrypoint-4.1.1-exec.sh', 'w').write(
    src.replace(old, '\nexec java ${ONTOP_JAVA_ARGS}'))
PY
chmod +x setup/vkg-system/entrypoint/entrypoint-4.1.1-exec.sh
```

Berkas ini **terikat pada versi 4.1.1**. Bila image Ontop di-upgrade, hapus mount
berkas ini dari `docker-compose.yaml` (Ontop 5.x sudah memakai `exec`).
