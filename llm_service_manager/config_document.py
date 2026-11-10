"""Four persisted setting scopes; existing consumers use one effective view."""

import copy

from .config import SCHEMA_VERSION, SPECS, Error, normalized, validate
from .paths import BACKENDS

GLOBAL_SETTINGS = frozenset(("host", "port", "autostart"))
METADATA = ("catalog", "model_selection", "target", "initial_port")


def backend_keys(backend):
    return {
        key
        for key, spec in SPECS.items()
        if key not in GLOBAL_SETTINGS and backend in spec[2]
    }


def scoped_values(c, keys):
    return {key: copy.deepcopy(c["settings"][key]) for key in SPECS if key in keys}


def for_backend(c, backend):
    """Select an independent editable view, preserving unsaved global values."""
    if backend not in BACKENDS:
        raise Error("未知底座")
    c = copy.deepcopy(c)
    banks = c.get("_backend_settings")
    if banks is None:
        # Pure adapter inputs already contain an effective settings view.
        return c
    previous = c["_settings_backend"]
    banks[previous] = scoped_values(c, backend_keys(previous))
    if previous != backend:
        global_values = scoped_values(c, GLOBAL_SETTINGS)
        c["settings"] = normalized({})["settings"]
        c["settings"].update(global_values)
        c["settings"].update(copy.deepcopy(banks[backend]))
    c["_settings_backend"] = backend
    return c


def decode(document):
    """Read only the current on-disk format; no historical schema migration."""
    if not isinstance(document, dict):
        raise Error("配置必须是 JSON 对象")
    if (
        type(document.get("schema", SCHEMA_VERSION)) is not int
        or document.get("schema", SCHEMA_VERSION) != SCHEMA_VERSION
    ):
        raise Error("不支持的配置版本")
    if set(document) - {"schema", "global", *BACKENDS, *METADATA}:
        raise Error("配置包含未知区域")
    global_block = document.get("global", {})
    if not isinstance(global_block, dict) or set(global_block) - {
        "runtime",
        "settings",
    }:
        raise Error("全局配置格式无效")
    global_values = global_block.get("settings", {})
    if not isinstance(global_values, dict) or set(global_values) - GLOBAL_SETTINGS:
        raise Error("全局参数格式无效")
    meta = {key: copy.deepcopy(document[key]) for key in METADATA if key in document}
    if "runtime" in global_block:
        meta["runtime"] = copy.deepcopy(global_block["runtime"])
    views = {}
    for backend in BACKENDS:
        block = document.get(backend, {})
        if not isinstance(block, dict) or set(block) - {"settings"}:
            raise Error("底座配置格式无效：" + backend)
        settings = block.get("settings", {})
        if not isinstance(settings, dict) or set(settings) - backend_keys(backend):
            raise Error("底座参数格式无效：" + backend)
        view = normalized(dict(meta, settings={**global_values, **settings}))
        validate(view)
        views[backend] = view
    backend = (meta.get("target") or {}).get("backend", "ollama")
    c = views[backend]
    c["_backend_settings"] = {
        b: scoped_values(v, backend_keys(b)) for b, v in views.items()
    }
    c["_settings_backend"] = backend
    return c


def encode(c):
    """Persist globals and the edited backend; never copy it into another backend."""
    backend = c.get(
        "_settings_backend", (c.get("target") or {}).get("backend", "ollama")
    )
    banks = copy.deepcopy(c.get("_backend_settings", {}))
    banks[backend] = scoped_values(c, backend_keys(backend))
    defaults = normalized({})
    document = {
        "schema": SCHEMA_VERSION,
        "global": {
            "runtime": copy.deepcopy(c["runtime"]),
            "settings": scoped_values(c, GLOBAL_SETTINGS),
        },
        **{
            b: {"settings": banks.get(b, scoped_values(defaults, backend_keys(b)))}
            for b in BACKENDS
        },
        **{key: copy.deepcopy(c[key]) for key in METADATA if key in c},
    }
    decode(document)
    return document
