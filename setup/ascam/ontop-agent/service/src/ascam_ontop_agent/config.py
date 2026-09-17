"""
Konfigurasi agen.

Artefak yang DIEKSPOS hanya ℳ (R2RML) dan 𝒯 (ontologi). Berkas properti Ontop memuat
kredensial JDBC, sehingga tidak pernah dibaca lewat API; berkas itu hanya dipakai di dalam
agen saat menjalankan `ontop validate`.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

MEDIA_TYPES = {'r2rml': 'text/turtle', 'ontology': 'text/turtle'}


@dataclass
class AgentConfig:
    artifacts_dir: Path
    mapping_name: str = 'mapping.ttl'
    ontology_name: str = 'ontology_file.ttl'
    properties_name: str = 'government.docker.properties'
    ontop_container: str = 'vkg-system-ontop-teiid'
    ontop_image: str = 'ontop/ontop-endpoint:4.1.1'
    ontop_input_dir: str = '/opt/ontop/input'          # path di dalam kontainer Ontop
    tokens_file: str | None = None
    names: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.names = {'r2rml': self.mapping_name, 'ontology': self.ontology_name}

    def path_of(self, kind: str) -> Path:
        if kind not in self.names:                      # mencegah penelusuran path sembarang
            raise KeyError(kind)
        return self.artifacts_dir / self.names[kind]

    def container_path(self, kind: str) -> str:
        return f'{self.ontop_input_dir}/{self.names[kind]}'

    @property
    def container_properties(self) -> str:
        return f'{self.ontop_input_dir}/{self.properties_name}'


def from_env() -> AgentConfig:
    return AgentConfig(
        artifacts_dir=Path(os.getenv('ASCAM_AGENT_ARTIFACTS_DIR', '/artifacts')),
        mapping_name=os.getenv('ASCAM_AGENT_MAPPING_NAME', 'mapping.ttl'),
        ontology_name=os.getenv('ASCAM_AGENT_ONTOLOGY_NAME', 'ontology_file.ttl'),
        properties_name=os.getenv('ASCAM_AGENT_PROPERTIES_NAME', 'government.docker.properties'),
        ontop_container=os.getenv('ASCAM_AGENT_ONTOP_CONTAINER', 'vkg-system-ontop-teiid'),
        ontop_image=os.getenv('ASCAM_AGENT_ONTOP_IMAGE', 'ontop/ontop-endpoint:4.1.1'),
        ontop_input_dir=os.getenv('ASCAM_AGENT_ONTOP_INPUT_DIR', '/opt/ontop/input'),
        tokens_file=os.getenv('ASCAM_AGENT_API_TOKENS_FILE'),
    )
