# Ontop 4.1.1 entrypoint with `exec java`

`entrypoint-4.1.1-exec.sh` is a copy of `/opt/ontop/entrypoint.sh` from the
`ontop/ontop-endpoint:4.1.1` image with **one** change, on the line that starts the JVM:

```diff
-java ${ONTOP_JAVA_ARGS} -cp ...
+exec java ${ONTOP_JAVA_ARGS} -cp ...
```

Without `exec`, bash stays the parent of the Java process and does not forward SIGTERM, so
`docker stop` and `docker restart` always wait for the timeout (10 s) and then send SIGKILL.
Ontop 5.0.0 already uses `exec java` in its official entrypoint; this file backports that
behaviour to 4.1.1.

To recreate the file (reproducible, taken from the image in use):

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

The file is **tied to version 4.1.1**. If the Ontop image is upgraded, remove the mount of
this file from `docker-compose.yaml` (Ontop 5.x already uses `exec`).
