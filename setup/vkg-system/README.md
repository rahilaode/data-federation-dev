# OBDA layer (Ontop, blue-green)

Ontop 4.1.1 exposes the federated data as a SPARQL endpoint, reading the mapping ℳ
(`config/mapping.ttl`, R2RML) and the ontology 𝒯 (`config/ontology_file.ttl`) over the Teiid VDB
`government` (`config/government.docker.properties`).

Two Ontop instances run behind an nginx proxy (ADR-0022):

| Container | Role |
|---|---|
| `vkg-system-ontop-teiid` | nginx proxy on port 8080; the SPARQL endpoint is `http://localhost:8080/sparql` |
| `vkg-system-ontop-blue` | Ontop instance that reads `slots/blue/` |
| `vkg-system-ontop-green` | Ontop instance that reads `slots/green/` |

Only the active instance runs. Each slot holds a copy of the mapping, the ontology, and the
properties, with the JDBC URL pinned to one VDB version (`;version=N`). `slots/state.json` records
the active colour and its VDB version; `proxy/runtime/upstream.conf` tells nginx which instance
to use. Both are created by `init-bluegreen.sh` and changed afterwards only by the ASCAM agent.
`config/` always mirrors the active instance and is the canonical copy that Knowledge syncs from.

```bash
setup/vkg-system/up.sh            # create missing slots, start the proxy and the active instance
setup/vkg-system/up.sh --reset    # rebuild both slots from config/ (used by start.sh)
```

`entrypoint/` holds the Ontop 4.1.1 entrypoint patched to `exec java`, so that the JVM stops
quickly (ADR-0001, see `entrypoint/README.md`). `jdbc/` holds the JDBC drivers, and
`wait-for-it/` the script that waits for Teiid before Ontop starts.

The artifacts in `config/` are the base state of the case study. ASCAM writes to them during an
adaptation; `git checkout -- setup/vkg-system/config/mapping.ttl setup/vkg-system/config/ontology_file.ttl`
(or `./start.sh`) brings them back.
