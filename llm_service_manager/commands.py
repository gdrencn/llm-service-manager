"""Official recipes shared by documentation and an explicitly selected executor."""

import shlex
from dataclasses import dataclass

from .presentation import boundary, render, rich, t

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
    groups = {
        "ollama": [
            (
                "ollama.install",
                t("安装官方 Ollama", "Install official Ollama"),
                "curl -fsSL https://ollama.com/install.sh | sh",
            ),
            (
                "ollama.model",
                t("下载完整模型包", "Download the complete model package"),
                "ollama pull " + shlex.quote(ollama_model),
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
                t(
                    "下载主模型、视觉和可用的 MTP 权重",
                    "Download model, vision and available MTP weights",
                ),
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
                    "下载 NVFP4 主模型、视觉和 MTP",
                    "Download NVFP4 model, vision and MTP",
                ),
                '"$HOME/.local/bin/strata" install -- --family nvfp4 --model NVFP4 --data-dir "$HOME/models/strata" --vision gpu --mtp on --drafter mtp --no-start --no-browser --yes',
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
    if backend == "ollama":
        return t(
            "通过 Ollama 服务下载指定模型包，不加载模型进行推理。",
            "Download the selected model package through the Ollama service without loading it for inference.",
        )
    if backend == "llama.cpp":
        return t(
            "下载指定量化的主模型权重，并自动下载对应视觉组件和可用的 MTP 权重。",
            "Download the selected quantized model weights with the matching vision component and available MTP weights.",
        )
    if "update --yes" in cmd:
        return t(
            "补齐官方推理引擎和视觉程序，供下一步模型安装使用。",
            "Install the official inference engine and vision executable needed by model setup.",
        )
    return t(
        "下载无后缀 NVFP4 的主模型、视觉权重及 MTP，保存到 ~/models/strata；完成后不启动模型。",
        "Download the unsuffixed NVFP4 model, vision weights and MTP into ~/models/strata without starting the model.",
    )


def introduction():
    return [
        t(
            "以下为官方命令，可自行复制执行；也可在末尾选择依次执行。",
            "Copy the official commands below, or choose sequential execution at the end.",
        ),
        t(
            "以配置中的用户运行。下载可能耗时数小时；同一时间只运行一条命令，失败即停止。",
            "Run as the configured user. Downloads may take hours. Commands run sequentially and stop on failure.",
        ),
        t(
            "执行入口只按顺序运行下方命令，不额外启停服务、设置底座参数或恢复配置。",
            "Execution runs only the commands below, without additional service actions, backend settings or configuration restoration.",
        ),
        t(
            "包含主模型、视觉和可用的 MTP；已有文件由官方工具处理。此流程未做从零下载自动验收。",
            "Includes model, vision and available MTP weights. Official tools handle existing files. Full downloads are not automatically tested.",
        ),
        t(
            "Ollama 官方安装器可能启动服务，pull 需要服务可用；若未就绪，命令会直接报错。",
            "The official Ollama installer may start its service; pull requires a reachable service and fails if it is unavailable.",
        ),
        t(
            "运行环境：配置中的运行用户、该用户主目录、Bash（pipefail）；命令及输出保留操作记录。",
            "Execution context: configured user, their home directory, Bash with pipefail; commands and output are logged.",
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
