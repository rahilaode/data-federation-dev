# Schema monitor transport (Kafka and Debezium)

| Container | Image | Host port |
|---|---|---|
| `ascam-sm-zookeeper` | `quay.io/debezium/zookeeper:3.1` | 2181 |
| `ascam-sm-kafka` | `quay.io/debezium/kafka:3.1` | 29092 |
| `ascam-sm-connector` | `quay.io/debezium/connect:3.1` (Kafka Connect REST API) | 8083 |
| `ascam-sm-kafka-ui` | `provectuslabs/kafka-ui` | 8081 |

`register-postgres.json` and `register-mysql.json` register one Debezium connector per source.
Both capture only the monitor log table (`table.include.list` = `schema_monitor.ddl_event_log`
and `dukcapil.ddl_event_log`) and use `snapshot.mode = no_data`, so only DDL recorded after the
registration is published. The topics are `kemensos.schema_monitor.ddl_event_log` and
`dukcapil.dukcapil.ddl_event_log`.

```bash
docker-compose -f setup/ascam/schema-monitor/docker-compose.yaml up -d
curl -s -X POST -H 'Content-Type: application/json' --data @setup/ascam/schema-monitor/register-postgres.json http://localhost:8083/connectors
curl -s -X POST -H 'Content-Type: application/json' --data @setup/ascam/schema-monitor/register-mysql.json http://localhost:8083/connectors
curl -s http://localhost:8083/connectors/postgres-connector/status
```

`start.sh` and `restart-ascam.sh` do this for you. `sample_event_postgresql/` and
`sample_event_mysql/` hold one captured Debezium message per operation, for reference.
If a connector loses its position, see `experiments/reset_connectors.py`.
