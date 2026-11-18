"""Official recipes shared by documentation and an explicitly selected executor."""

import shlex
from dataclasses import dataclass

from .presentation import boundary, render, rich, t

OLLAMA_UNSLOTH_27B = "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL"

SOURCES = {
    "ollama": "https://ollama.com/download/linux",
    "llama.cpp": "https://github.com/ggml-org/llama.cpp",
    "strata": "https://github.com/Niko1221/Strata",
}


@dataclass(frozen=True)
class Recipe:
    key: str
    backend: str
    title: str
    command: str

    @property
    def entry(self):
        return self.backend, self.title, self.command


def recipe_specs(
    backend="all",
    ollama_model="qwen3.8",
    hf_model="unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL",
):
    download = t("下载 ", "Download ")
    groups = {
        "ollama": [
            (
                "ollama.install",
                t("安装官方 Ollama", "Install official Ollama"),
                "curl -fsSL https://ollama.com/install.sh | sh",
            ),
            (
                "ollama.model",
                download + ollama_model,
                "ollama pull " + shlex.quote(ollama_model),
            ),
            (
                "ollama.unsloth_27b",
                download + OLLAMA_UNSLOTH_27B.removeprefix("hf.co/"),
                "ollama pull " + OLLAMA_UNSLOTH_27B,
            ),
        ],
        "llama.cpp": [
            (
                "llama.cpp.install",
                t("安装官方 llama.cpp", "Install official llama.cpp"),
                "curl -LsSf https://llama.app/install.sh | sh",
            ),
            (
                "llama.cpp.model",
                download + hf_model,
                '"$HOME/.local/bin/llama" download -hf '
                + shlex.quote(hf_model)
                + " --mmproj-auto --mtp",
            ),
        ],
        "strata": [
            (
                "strata.install",
                t("安装官方 Strata 命令行工具", "Install the official Strata CLI"),
                "curl -fsSL https://github.com/Niko1221/Strata/releases/latest/download/install.sh -o strata-install.sh && sh strata-install.sh",
            ),
            (
                "strata.engine",
                t("安装引擎及视觉程序", "Install engine and vision executable"),
                '"$HOME/.local/bin/strata" update --yes',
            ),
            (
                "strata.model",
                t(
                    "下载 Qwen3.8-Flash-Next（NVFP4）",
                    "Download Qwen3.8-Flash-Next (NVFP4)",
                ),
                '"$HOME/.local/bin/strata" install -- --family nvfp4 --model NVFP4 --data-dir "$HOME/models/strata" --vision gpu --mtp on --drafter mtp --no-start --no-browser --yes',
            ),
            (
                "strata.iq3_s",
                t(
                    "下载 Qwen3.8-Flash-Next（ISTA-DASLab IQ3_S）",
                    "Download Qwen3.8-Flash-Next (ISTA-DASLab IQ3_S)",
                ),
                '"$HOME/.local/bin/strata" install -- --family qwen --model IQ3_S --data-dir "$HOME/models/strata" --vision gpu --mtp on --drafter mtp --no-start --no-browser --yes',
            ),
        ],
    }
    return [
        Recipe(key, b, title, cmd)
        for b in (groups if backend == "all" else [backend])
        for key, title, cmd in groups[b]
    ]


def recipes(
    backend="all",
    ollama_model="qwen3.8",
    hf_model="unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL",
):
    return [item.entry for item in recipe_specs(backend, ollama_model, hf_model)]


def explanation(backend, cmd):
    if "curl " in cmd:
        return t(
            "下载安装官方底座程序，不下载模型权重。",
            "Install the official backend software; model weights are downloaded separately.",
        )
    if cmd == "ollama pull " + OLLAMA_UNSLOTH_27B:
        return t(
            "模型包包含视觉组件，MTP 权重已内嵌。",
            "Includes the vision projector and embedded MTP weights.",
        )
    if backend == "ollama":
        return t(
            "通过 Ollama 服务下载指定模型包，不加载模型进行推理。",
            "Download the selected model package through the Ollama service without loading it for inference.",
        )
    if backend == "llama.cpp":
        return t(
            "同时下载配套视觉组件和可用的 MTP 权重。",
            "Download the selected quantized model weights with the matching vision component and available MTP weights.",
        )
    if "update --yes" in cmd:
        return t(
            "补齐官方推理引擎和视觉程序，供下一步模型安装使用。",
            "Install the official inference engine and vision executable needed by model setup.",
        )
    if "--model IQ3_S" in cmd:
        return t(
            "下载 ISTA-DASLab IQ3_S 全部分片、视觉和 MTP，准备运行包及 tokenizer；保存到 ~/models/strata，完成后不启动模型。",
            "Download all ISTA-DASLab IQ3_S shards, vision and MTP; prepare the runtime pack and tokenizer in ~/models/strata without starting the model.",
        )
    return t(
        "包含视觉组件和 MTP，保存到 ~/models/strata，完成后不启动模型。",
        "Download the unsuffixed NVFP4 model, vision weights and MTP into ~/models/strata without starting the model.",
    )


def introduction():
    return [
        t(
            "可复制下方官方命令，也可在安装清单中选中一项执行。",
            "Copy an official command below or select an item in the installation menu.",
        ),
        t(
            "自动安装按顺序执行未安装项，出错即停止。命令和输出会记入操作记录。",
            "Automatic installation runs missing items in order and stops on error. Commands and output are logged.",
        ),
        t(
            "执行 ollama pull 前，请确保 Ollama 服务可访问。",
            "Make sure the Ollama service is reachable before running ollama pull.",
        ),
    ]


def commands(
    backend="all",
    ollama_model="qwen3.8",
    hf_model="unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL",
):
    lines = introduction() + [""]
    for i, (b, title, cmd) in enumerate(recipes(backend, ollama_model, hf_model), 1):
        lines += [f"{i}. {b} · {title}", explanation(b, cmd), SOURCES[b], cmd, ""]
    return "\n".join(lines)


def execute(backend, c, items=None):
    """Run exactly the displayed recipes; no implicit backend lifecycle actions."""
    import os
    import pwd
    import subprocess
    import sys

    from .config import Error
    from .execution import Operations as Ops

    user = pwd.getpwnam(c["runtime"]["user"])
    audit = Ops(quiet=True)
    for i, (b, title, cmd) in enumerate(
        recipes(backend) if items is None else items, 1
    ):
        boundary(f"{i}. {b} · {title}", tone="section")
        print(render(rich(("$ " + cmd, "command"))), flush=True)
        # The menu runs as root; sudo -H executes the visible command as the regular user.
        argv = [
            "sudo",
            "-H",
            "-u",
            user.pw_name,
            "--",
            "bash",
            "-o",
            "pipefail",
            "-c",
            cmd,
        ]
        audit.event("command", argv=argv, cwd=user.pw_dir)
        proc = subprocess.Popen(
            argv, cwd=user.pw_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        try:
            while True:
                data = os.read(proc.stdout.fileno(), 8192)
                if not data:
                    break
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
                audit.event("install-output", output=data.decode(errors="replace"))
            code = proc.wait()
            audit.event("result", exit=code)
            if code:
                raise Error(
                    t(
                        "命令失败，后续命令未执行。",
                        "Command failed; remaining commands were not run.",
                    )
                    + f" ({code})"
                )
        except BaseException:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            raise
        finally:
            proc.stdout.close()
    print(
        t("\n以上命令已依次执行完成。", "\nAll displayed commands completed in order.")
    )
