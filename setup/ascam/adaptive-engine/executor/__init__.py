"""
ASCAM Executor
==============
Realisasi fase Execute (dan penutupan loop melalui Verify) pada MAPE-K.

Modul:
  artifact_store  : atomic write + snapshot/revert artefak F = (T, M, Σ_S)
  teiid_deployer  : effector Σ'_S  -> redeploy VDB via marker file scanner WildFly
  ontop_controller: effector T', M' -> reload Ontop (restart kontainer)
  verifier        : pemeriksaan pasca-eksekusi melalui endpoint SPARQL
  executor        : urutan eksekusi, pencatatan waktu, dan revert
"""
from .executor import AdaptationPlan, AdaptationExecutor, ArtifactChange, VerificationCheck

__all__ = ['AdaptationPlan', 'AdaptationExecutor', 'ArtifactChange', 'VerificationCheck']
