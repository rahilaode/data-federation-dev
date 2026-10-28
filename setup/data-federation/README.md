# Data federation (Teiid)

Teiid 16.0.0 on WildFly 19.1.0 federates the two sources into the VDB `government`
(`deployments/government-vdb.xml`), which holds the federated schema Σ_S:

| Model | Kind | Content |
|---|---|---|
| `kemensos` | physical (PostgreSQL) | foreign tables `program_bansos`, `penerima_manfaat`, `transaksi_bansos`, `eligibility_check` |
| `dukcapil` | physical (MySQL) | foreign tables `master_wilayah`, `master_keluarga`, `master_penduduk`, `riwayat_perubahan_data` |
| `layanan` | virtual | views `v_penerima_aktif`, `v_riwayat_perubahan`, `v_program_berakhir` (scenarios A007–A009) |

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
deployed from the mounted `deployments/` folder when a `government-vdb.xml.dodeploy` marker is
present (`start.sh` creates it).

VDB versions created by ASCAM are deployed through the management API, not written to
`deployments/`, so `government-vdb.xml` stays the base state. WildFly keeps them in the volume
`teiid-standalone-data`; `./start.sh` removes it, `./restart-ascam.sh` keeps it.
