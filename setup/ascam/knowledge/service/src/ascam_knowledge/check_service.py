"""Run connection checks for registered targets and record the results."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import connectors
from .db import registry
from .security.crypto import SecretBox


class Hooks:
    """Injection points for tests (HTTP client, connect function, Kafka admin)."""
    http = None
    connect = None
    kafka_admin_factory = None


def run_check(db: Session, target: registry.Target, box: SecretBox, actor: str,
              hooks: type[Hooks] = Hooks) -> registry.ConnectionCheck:
    # The check time comes from the application clock when the check starts. PostgreSQL now()
    # returns the START of the transaction, so several checks in one request would share it.
    checked_at = datetime.now(timezone.utc)
    username = password = None
    if target.credential_id is not None:
        cred = db.get(registry.Credential, target.credential_id)
        username, password = cred.username, box.decrypt(cred.secret_ciphertext)

    ep = target.endpoint
    if not target.enabled:
        result = connectors.CheckResult(False, 0, 'target disabled; not checked')
    elif target.kind == 'teiid_mgmt':
        result = connectors.check_teiid_mgmt(ep, username, password, http=hooks.http)
    elif target.kind == 'teiid_odbc':
        result = connectors.check_teiid_odbc(ep, username, password, connect=hooks.connect)
    elif target.kind == 'ontop_sparql':
        result = connectors.check_ontop_sparql(ep, http=hooks.http)
    elif target.kind == 'kafka':
        topics = [t for t in db.execute(select(registry.SourceSystem.kafka_topic)
                                        .filter_by(obdf_id=target.obdf_id)).scalars() if t]
        result = connectors.check_kafka(ep, topics, admin_factory=hooks.kafka_admin_factory)
    elif target.kind == 'ontop_agent':
        # the agent credential's secret is a bearer token (not a database password)
        result = connectors.check_ontop_agent(ep, token=password, http=hooks.http)
    else:                                                    # prevented by a CHECK constraint
        result = connectors.CheckResult(False, 0, f'unknown target kind {target.kind}')


    row = registry.ConnectionCheck(target_id=target.id, checked_at=checked_at, ok=result.ok,
                                   latency_ms=result.latency_ms, detail=result.detail(), actor=actor)
    db.add(row)
    db.flush()
    db.refresh(row)
    return row
