"""Fixed native settings shared by configuration rendering and presentation."""


def fixed_environment(backend):
    if backend == "strata":
        return {"STRATA_ROUTE_TAIL_SKIP": "0", "STRATA_ROUTE_PRIOR": "0"}
    return {}


def authentication_disabled(backend):
    return backend in ("strata", "llama.cpp")


def unset_environment(backend):
    return {
        "strata": ("STRATA_API_KEY",),
        "llama.cpp": ("LLAMA_API_KEY", "LLAMA_ARG_API_KEY_FILE"),
    }.get(backend, ())
