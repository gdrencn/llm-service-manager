"""Render official ollama configuration. No I/O except the supplied source reader."""

import re

from ..config import Error, address, model, value
from ..paths import OLLAMA_DROPIN, OLLAMA_MODELFILE
from .common import unit_quote


def render(c, read):
    b = "ollama"
    m = model(c, b)
    out = {}

    def v(key):
        return value(c, key)

    env = {
        "OLLAMA_HOST": address(v("host"), v("port")),
        "OLLAMA_CONTEXT_LENGTH": v("context"),
        "OLLAMA_KV_CACHE_TYPE": v("kv"),
        "OLLAMA_FLASH_ATTENTION": int(v("flash_attention")),
        "OLLAMA_KEEP_ALIVE": str(v("idle_seconds")) + "s"
        if v("idle_seconds")
        else "-1",
        "OLLAMA_NUM_PARALLEL": v("parallel"),
        "OLLAMA_MAX_LOADED_MODELS": 1,
        "OLLAMA_GPU_OVERHEAD": v("reserve_mib") * 1024 * 1024,
    }
    out[OLLAMA_DROPIN] = (
        "# Managed by llmsm\n[Unit]\nConflicts=llm-llama.service llm-strata.service\n[Service]\n"
        + "".join(
            "Environment=" + unit_quote(k + "=" + str(x), expand_dollar=False) + "\n"
            for k, x in env.items()
        )
    )
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", m["source"]):
        raise Error("Ollama模型名无效")
    out[OLLAMA_MODELFILE] = (
        f"FROM {m['source']}\nPARAMETER num_ctx {v('context')}\nPARAMETER num_gpu 999\nPARAMETER draft_num_predict {v('mtp_candidates') if v('mtp') else 0}\n"
    )
    return out
