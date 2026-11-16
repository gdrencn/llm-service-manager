"""Fixed native settings shared by configuration rendering and presentation."""


def fixed_environment(backend):
    if backend == "strata":
        return {"STRATA_ROUTE_TAIL_SKIP": "0", "STRATA_ROUTE_PRIOR": "0"}
    return {}
