# Data federation (Teiid)

Teiid 16.0.0 on WildFly 19.1.0 federates the two sources into the VDB `example`
(`deployments/example-vdb.xml`), which holds the federated schema Σ_S of the running example
(`docs/running-example.md`):

| Model | Kind | Content |
|---|---|---|
| `src` | physical (PostgreSQL `reporting`, JNDI `java:/pgsql-reporting`) | 12 foreign tables |
| `reg` | physical (MySQL `registry`, JNDI `java:/mysql-registry`) | foreign table `member` |
| `v` | virtual | views `v_active`, `v_net_price`, `v_note_body`, `v_memo`, `v_doc_cache`, `v_city_all`, `v_events`, one for each case of the Plan that concerns views |

The foreign tables are declared with explicit DDL (`CREATE FOREIGN TABLE`) with primary keys.
All names are lower case, so the columns need no `NAMEINSOURCE` (src(ĉ) = ĉ). The one exception is
`staff.town_legacy`, which reads the source column `town` that does not exist; it makes the rename
of r05 a notification (c′ is already read by another column).

| Host port | Use |
|---|---|
| 31000 | Teiid JDBC (Ontop connects here) |
| 35432 | Teiid ODBC, PostgreSQL protocol (Knowledge reads `SYS.*` and `SYSADMIN.*` here) |
| 19990 | WildFly management API (the Executor deploys VDB versions here) |
| 18080 | WildFly HTTP |

The image (`Dockerfile`) downloads Teiid from Maven Central and checks its SHA-1, creates the
users (`admin` in ManagementRealm, `user1` in ApplicationRealm), and runs
`virtual-db-config/setup.cli`, which installs the JDBC drivers and datasources and puts the
deployment scanner in manual mode for XML files. The VDB is not baked into the image: it is
deployed from the mounted `deployments/` folder when an `example-vdb.xml.dodeploy` marker is
present (`start.sh` creates it). `setup.cli` is baked into the image, so a change to it needs
`docker-compose build` (`start.sh` builds).

The container runs with `-Xmx1g -Xss16m` (`JAVA_OPTS` in `docker-compose.yaml`). Teiid resolves a
UNION chain recursively, and with the default 1 MB thread stack a query such as
`SELECT * WHERE { ?s a ?c }` over the large NPD mapping (about 1,500 UNION branches) fails with
TEIID30019 `StackOverflowError` (ADR-0030). The setting is kept on this branch.

VDB versions created by ASCAM are deployed through the management API, not written to
`deployments/`, so `example-vdb.xml` stays the base state. WildFly keeps them in the volume
`teiid-standalone-data`; `./start.sh` removes it, `./restart-ascam.sh` keeps it.
