"""Shared state decision for checked, actionable setup items."""

from enum import Enum

from .presentation import t


class State(str, Enum):
    EFFECTIVE = "effective"
    EXECUTE = "execute"
    MANUAL = "manual"


def state(checked, can_execute):
    if checked is True:
        return State.EFFECTIVE
    if checked is False and can_execute:
        return State.EXECUTE
    return State.MANUAL


def label(value):
    return {
        State.EFFECTIVE: (t("✓ 已生效", "✓ Effective"), "good"),
        State.EXECUTE: (t("执行", "Execute"), "action"),
        State.MANUAL: (t("请手动执行命令", "Run the commands manually"), "muted"),
    }[value]
