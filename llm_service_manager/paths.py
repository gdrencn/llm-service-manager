"""Tool metadata is disposable; backend configuration must survive its removal."""

from pathlib import Path

CONFIG = Path("/etc/llm-service-manager/config.json")
STATE = Path("/var/lib/llm-service-manager")
PREFERENCES = Path("/etc/llm-service-manager/ui.json")
STRATA_CONFIG = "/etc/strata/strata-local.json"
OLLAMA_MODELFILE = "/etc/ollama/ollama-local.Modelfile"
OLLAMA_DROPIN = "/etc/systemd/system/ollama.service.d/ollama-local.conf"
BACKENDS = ("ollama", "llama.cpp", "strata")
UNITS = {
    "ollama": "ollama.service",
    "llama.cpp": "llama-server-local.service",
    "strata": "strata-local.service",
}
LEGACY_UNITS = {
    "llama.cpp": "llm-llama.service",
    "strata": "llm-strata.service",
}
ALL_UNITS = tuple(UNITS.values()) + tuple(LEGACY_UNITS.values())


def existing_unit(backend):
    """Read old installations before their next transactional configuration apply."""
    current = UNITS[backend]
    legacy = LEGACY_UNITS.get(backend)
    if legacy and not Path("/etc/systemd/system", current).exists():
        if Path("/etc/systemd/system", legacy).exists():
            return legacy
    return current


def ollama_roots(home):
    return [Path("/usr/share/ollama/.ollama/models"), Path(home) / ".ollama/models"]


def hf_cache(home):
    return Path(home) / ".cache/huggingface/hub"


def strata_install(home):
    return Path(home) / ".local/share/strata/install"


def strata_engines(home):
    return Path(home) / ".local/share/strata/components/engine"
