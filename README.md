# llm-service-manager 0.10.0 test

独立开发的 Linux/systemd 本地模型服务管理工具。管理 Ollama、llama.cpp 和 Strata，
一份共享配置，任一时间仅运行一个底座。仅管理服务端底座及模型。

## 一条命令开始（test）

支持 apt/dnf 系 Linux（如 Ubuntu 22.04+、Debian 12+、较新的 Fedora），Python 3.10+。
目标是全新机器：不要求预装 Python、sudo、curl、模型或底座。下面是一段可整体粘贴执行的入口命令，
缺 curl/CA 证书时先通过系统包管理器补齐，再获取安装器。安装器自动补齐其余工具依赖。
当前仅发布 **0.10.0 test**（GitHub Pre-release，标签 `v0.10.0`），尚无 stable。
下面入口固定安装该 test，不跟随 main 的未发布代码。普通 `install.sh` 在没有 stable 时明确退出，不会偷偷安装 test。

<!-- bootstrap:start -->
```sh
sh <<'LLMSM_BOOTSTRAP'
#!/bin/sh
# Also embedded verbatim in README.md. Only obtains the official project installer.
set -eu
as_root() {
 if [ "$(id -u)" = 0 ]; then "$@"
 elif command -v sudo >/dev/null 2>&1; then sudo -- "$@"
 elif command -v su >/dev/null 2>&1; then
  llmsm_cmd=
  for llmsm_arg do
   llmsm_quoted=$(printf '%s' "$llmsm_arg" | sed "s/'/'\\\\''/g")
   llmsm_cmd="$llmsm_cmd '$llmsm_quoted'"
  done
  su -c "exec $llmsm_cmd"
 else echo 'Administrator access is required; run as root.' >&2; exit 1
 fi
}

if ! command -v curl >/dev/null 2>&1 || { [ ! -f /etc/ssl/certs/ca-certificates.crt ] && [ ! -f /etc/pki/tls/certs/ca-bundle.crt ]; }; then
 if command -v apt-get >/dev/null 2>&1; then
  as_root apt-get update
  as_root apt-get install -y ca-certificates curl
 elif command -v dnf >/dev/null 2>&1; then as_root dnf install -y ca-certificates curl
 else echo 'No supported package manager (apt/dnf). Cannot provision curl automatically.' >&2; exit 1
 fi
fi
llmsm_bootstrap=$(mktemp)
trap 'rm -f "$llmsm_bootstrap"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
curl -fsSL https://raw.githubusercontent.com/gdrencn/llm-service-manager/v0.10.0/test/install.sh -o "$llmsm_bootstrap"
sh "$llmsm_bootstrap"
LLMSM_BOOTSTRAP
```
<!-- bootstrap:end -->

已有 curl 的机器可使用短命令（与上面的入口调用同一个安装器）：

```sh
curl -fsSL https://raw.githubusercontent.com/gdrencn/llm-service-manager/v0.10.0/test/install.sh | sh
```

安装完成后运行 `llmsm`。安装器只安装管理工具及其运行依赖，不安装推理底座、下载模型或启动模型服务。
以普通用户执行时使用 sudo；缺 sudo 时尝试 su 获取管理员权限，再安装 sudo。
以 root 执行也受支持；通过 `LLMSM_USER` 可指定已有账户作为底座运行用户。
不会创建隐藏账户、修改 sudoers、保存密码或自动取得系统没有授予的权限。

软件包可自动安装，管理员权限、正在运行的 init 系统、硬件和宿主机 GPU 驱动不能凭空提供。
安装器检查 systemd 是否正在运行；WSL 未启用 systemd 时明确要求先启用并重启发行版。
不支持的包管理器或过旧 Python 会明确报错，不尝试替换操作系统。
安装下载流程未执行从零自动验收；仅完成命令依据检查、生成一致性及 shell 语法检查。

首次进入没有底座、没有模型、没有已选目标是正常状态。
从“安装大模型和底座”使用展示的官方命令安装，再进入“切换底座/模型”配置。
缺失底座不会出现在切换菜单；安装清单仍会显示其安装入口。

## 交互入口

运行 `llmsm`。主菜单为：

* 切换底座/模型
* 安装大模型和底座
* 查看当前模型和服务状态
* 停止模型并释放资源
* 选项
* 退出

菜单和配置输入共用原地刷新区域，不进入全屏。保留输出采用统一空行、标题及分隔线；分隔线上下各留一个空行。
产品页标题专用青色加粗；统一加强色为 Codex 默认深色 Markdown 加粗文字所用的 #F38BA8，命令用粉色常规字、内容分段标题用粉色加粗；配置及统计值白色加粗；普通文本白色，说明及不可用项灰色。
所有菜单和输入框统一使用背景高亮表示当前选择；成功状态采用 #A6E3A1，链接采用 #89B4FA；警告和错误分别为黄、红。
配置项同时保留可修改、固定、不适用等文字标签，NO_COLOR 可禁用颜色。
菜单显示时隐藏终端光标；退出、取消或交给外部命令时恢复。文本编辑保留自绘输入位置指示。
方向键选择，Enter 确认，Esc 返回。文本预填当前值，Ctrl+U 清空。返回/取消总在最后，
默认焦点独立指定。选项仅提供中文/English 语言切换和版本信息。
语言保存到 /etc/llm-service-manager/ui.json，不改变底座配置、不重启服务。官方工具的输出保留其原语言。

选择底座和模型后编辑配置。末尾是“应用配置并启动模型”和“取消并退出”。
应用时检查文件、参数和互斥条件，通过后立即启动，不再展示第二份预览或再次确认。
检查失败返回编辑；取消不保存修改、不停止服务。状态页是只读快照，不发送推理请求。
分别展示保存的参数、服务状态、接口报告的驻留状态、服务内存/运行时间及整机资源。
无法获得的指标显示为未知，不把进程运行、已选模型和真正已加载模型混为一谈。

## 安装大模型和底座

默认只显示必要的安装、下载命令，每条附简短解释。Ollama/llama.cpp 各两条，Strata 三条。
交互指南使用全宽清单，每项显示底座、命令和说明，末尾显示绿色勾或“执行”。
方向键选择，Enter 执行，R 刷新，Esc 返回。底部提供“自动安装全部项”和“返回”。
自动安装跳过已安装项，按显示顺序串行执行，出错中断。
执行前隐藏清单，透传命令输出；成功后刷新本地状态并返回清单。
长命令自动折行，清单随选中项滚动。终端无法完整显示所选命令时不允许执行。
命令行 commands 子命令仍保留可复制文本。
执行入口只运行该行显示的命令。没有额外的服务启动/停止、
接口轮询、底座环境变量注入、自启修改或配置恢复。官方安装命令本身的行为由官方工具决定。
Ollama 安装器可能启动服务；pull 需要服务可用，未就绪就按原命令报错，不自动补救。

菜单使用 sudo -H 以配置中的运行用户运行 Bash（pipefail），工作目录是该用户主目录。
这是页面明确说明的执行环境；程序记录实际命令和输出。安装器要求的提权由其自身处理。
Strata doctor 是可选诊断，不列为安装下载必经步骤。安装与模型服务管理是独立入口。

目前预置：Ollama qwen3.8；llama.cpp unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL，
包括自动视觉组件和可用 MTP；Strata Qwen3.8-Flash-Next NVFP4，包括引擎、视觉程序、主模型、
视觉权重和MTP。自定义模型可自行使用官方工具安装，再进入模型选择。
自动发现范围为已支持的本地 Ollama manifest、Hugging Face GGUF 缓存、Strata 安装配置。
不提供手动登记菜单；任意自定义目录不保证自动发现。未确认的模型能力不会臆测为支持 MTP。

官方来源（2026-11-08 核对；完整安装/大模型下载不作为自动测试）：

* https://ollama.com/download/linux
* https://github.com/ggml-org/llama.cpp
* https://github.com/Niko1221/Strata
* https://github.com/Niko1221/Strata/blob/main/docs/NVFP4.md

命令输出和执行共用同一份 recipes，不维护两套命令。安装器可随官方更新；
静态核对、已安装版本的帮助及此前补齐记录不能等同于新版本从零安装验收。
本次开发不执行安装或下载。

## 命令行接口


```sh
llmsm status
llmsm status --json
llmsm commands --backend strata
llmsm commands --backend llama.cpp --hf-model unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL
llmsm models
llmsm preview strata --set context=256k
llmsm switch llama.cpp --set context=128k --yes
llmsm stop
llmsm --version
```

commands 子命令只打印，不执行。menu 是交互入口，旧 tui 子命令兼容保留，也使用行内菜单。
recover 是异常中断后的命令行修复入口，不作为普通菜单选项；仅存在未完成事务时才需要。

## 配置与适配

/etc/llm-service-manager/config.json 每项有 value 和 applies_to。共有项只有一个值，128K就是全部128K，
256K就是全部256K；不保存每模型的参数覆盖。模型目录只记录身份、文件路径、来源与能力。

Strata q8_0 映射 int8；idle_seconds=0 映射为 Ollama -1、llama.cpp -1、Strata 0。
Strata idle_unload_s 是服务配置；PLE ram 和服务 LimitMEMLOCK=infinity 分别控制驻留策略及锁定权限。
llama.cpp 显存预留依赖 fit 开启；fit 关闭时该项显示不生效且不能修改。
fit 和 MTP KV 精度仅作用于 llama.cpp；不把 LLAMA_ARG_* 透传给 Ollama 内部 runner。
Ollama GPU_OVERHEAD 是估算预留，非硬显存上限；Strata 预留影响专家缓存预算。
Strata 保留 CPU/GPU 混合；Ollama/llama.cpp 要求全部模型层在 GPU。
Strata 视觉可能在显存不足时回退 CPU，底座发出的诊断会显示并记录。

## 运行边界与开机启动

llmsm 是即用即走的配置工具。服务启动及客户端请求不经过 llmsm，
没有 llmsm 常驻进程、启动钩子、请求代理、重试生成或输出加工。
应用配置时调用官方命令、等待就绪并检查加载状态；不会发送测试聊天或生成文本。
Ollama 应用时使用官方空请求预加载模型，随后检查 GPU 驻留。这只发生在本次工具操作中。

共享选项“开机启动选定底座”仅控制 systemd enable/disable；其它底座关闭自启。
Ollama 保留官方服务及其重启策略，仅通过官方支持的 drop-in 配置环境变量。
开机启动 Ollama 服务不会自动预加载选定模型；客户端请求对应模型时加载。
llama.cpp 和 Strata 的 systemd 服务直接执行官方前台 CLI：
`llama serve --model …` 和 `strata serve --profile …`，模型随官方服务加载。
systemd 单元及参数文件由 llmsm 写入，属于系统配置；不是三个项目共同发布的官方服务模板。
Strata 不再调用内部 Python 模块，不固定其 Python 版本或安装版本目录。

“停止模型”只停止服务，不取消保存的自启选项；空闲卸载仍由底座负责。
这里的“开机”指 Linux/systemd 启动；WSL 中指发行版启动，不包含 Windows 自动启动 WSL。
官方安装入口仍只执行显示的官方命令，不混入服务配置或启停动作。

官方依据：

- [Ollama Linux 服务与配置](https://docs.ollama.com/linux)
- [Ollama 官方预加载 API](https://docs.ollama.com/faq#how-can-i-preload-a-model-into-ollama-to-get-faster-response-times)
- [llama.cpp 服务参数](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
- [Strata 官方前台服务命令](https://github.com/Niko1221/Strata/blob/main/docs/TERMINAL.md)

## 事务与日志

启动前检查模型/分片/视觉/MTP/引擎，应用时为已安装且配置有效的底座生成原生配置，只启动选定服务并检查就绪与加载状态。
Ollama 检查 size_vram，llama.cpp 检查全层 GPU 加载日志。错误或中断恢复之前配置及服务。
不自动缩小上下文或降低参数；不保证预检查能预测全部 OOM。恢复不保证保留旧 KV 会话。

命令、文件变更和接口访问记录到 /var/lib/llm-service-manager/audit.jsonl；界面显示简明进度。
进程被强杀后若留有 pending.json，可按错误提示执行 llmsm recover。
工具不修改 sudoers、登录身份、全局 memlock 或客户端设置，不调用外层虚拟化接口。
服务启动目标由工具独占管理；不要同时用其它工具覆盖服务配置。

原生配置路径：

* /etc/systemd/system/llm-llama.service
* /etc/systemd/system/ollama.service.d/llm.conf
* /etc/ollama/llmsm.Modelfile
* /etc/strata/llmsm.json
* /etc/systemd/system/llm-strata.service

## 测试

`python3 -m unittest discover -s tests -v`

保留配置映射、事务提交/恢复及终端交互测试；安装下载不作端到端自动测试。
配置/切换/停止回归覆盖缺失底座、损坏配置、文件写入失败、启动回滚、停止失败及自启。
本机验收日志和模型路径不放入公开项目，部署和测试记录保存在项目目录之外。

## 删除工具不会删除服务配置

程序 `/opt/llm-service-manager`、入口 `/usr/local/bin/llmsm`、工具元数据
`/etc/llm-service-manager` 和 `/var/lib/llm-service-manager` 均不在底座运行依赖链上。
底座需要保留的是操作结果：其官方程序、模型文件、systemd 单元及独立的原生配置：
`/etc/strata/llmsm.json`、`/etc/ollama/llmsm.Modelfile`、Ollama service drop-in。
模型服务日志位于底座的目录，不放在工具操作记录目录。
删除这些原生配置当然会影响服务；它们与删除管理工具是两件事。

## 开发与结构

运行时只依赖 Python 标准库。模块分工：

| 模块 | 唯一职责 |
|---|---|
| `config.py` / `paths.py` | 共享参数、校验、解析、路径定义 |
| `storage.py` | 同一快照读取与版本标记、原子写入、锁 |
| `artifacts.py` | GGUF、分片、manifest、Strata 必需文件检查 |
| `availability.py` / `discovery.py` / `inventory.py` | 程序可用性、本地模型发现、安装状态 |
| `adapters/` | 各底座的官方参数和原生配置渲染，无副作用 |
| `execution.py` | 命令、文件及 HTTP 操作与审计 |
| `services.py` | 官方服务生命周期、就绪和 GPU 驻留检查 |
| `manager.py` | 配置切换事务、提交和失败恢复 |
| `inline_ui/` / `tui.py` / `presentation.py` / `install_guide.py` / `status.py` | 行内交互、样式和只读展示 |
| `scripts/` | 从共享模板生成安装入口及 README 命令 |

```sh
python3 -m unittest discover -s tests -q
python3 scripts/build_installers.py --check
sh -n install.sh
sh -n bootstrap.sh
# 开发环境可另装 Ruff；它不是产品运行依赖。
ruff check inline_ui llm_service_manager tests scripts examples
ruff format --check inline_ui llm_service_manager tests scripts examples
```

修改安装模板后执行 `python3 scripts/build_installers.py`；不要手工维护多份同类引导代码。
测试不运行安装器、不下载模型。回归覆盖空环境、部分底座、缺失模型、失败回滚、损坏恢复记录、
写入失败、并发版本冲突、取消、终端恢复，以及底座不依赖工具程序或元数据的边界。

模型菜单只列出核心文件完整的模型（含 GGUF 分片和 Strata tokenizer）。
视觉和 MTP 文件缺失时仍可进入配置以关闭对应功能；启用时，启动前检查会阻止不完整配置。

## 可复用行内 UI

交互框架已独立为 [`inline_ui`](inline_ui/README.md)，只依赖 Python 标准库，不依赖 llmsm。
可整体复制到其它项目。接口、视觉规范、异常与取消语义详见该文档；
[最小示例](examples/inline_ui_demo.py)展示内容和业务回调如何绑定。
应用层负责配置、模型、命令及状态，框架不保存任何产品数据，也不提供常驻运行服务。

## 局域网访问与默认端口

全新配置默认监听 `0.0.0.0:55555`。首次初始化时，如果 Linux 中该端口已被占用，
依次尝试 55556、55557，最多到 65535；配置页标明自动递增原因和最终端口。
只在没有保存端口时选择一次，已有地址和用户手动指定的端口不会自动修改。
探测结束立即释放套接字，不预留端口；实际启动时再次检查，若发生竞争就报错并回滚。
Linux 能绑定不等于 Windows 同号端口可用，也不代表局域网已经连通。

主菜单“局域网访问”有两个入口，以及最下面的“返回”：

- **完成服务器端设置**：先实际连接本机非回环 IPv4 的配置端口。能连接则直接完成，不安装工具、不写配置、不重启底座。失败时按需应用 `0.0.0.0` 监听并启动选定模型，再检查防火墙；缺工具时使用发行版 apt/dnf 软件包。最终以再次 TCP 连接成功为准。没有模型、没有路由、未知端口占用或自定义规则无法安全处理时明确报错。
- **Windows端设置(WSL专属)**：只在 WSL 显示。信息页沿用清单样式，展示一键命令和说明，只有返回，没有执行按钮。进入页面不调用 Windows PowerShell。

`llmsm lan --setup-server` 与菜单的服务器设置使用同一个实现。
`llmsm lan` / `--json` 仍可查看诊断快照；`--configure-linux` 是单独的防火墙配置入口。
本机 TCP 连接成功不能证明外部流量通过所有防火墙；Windows 脚本和其它电脑仍需分别验证。
不修改 IP、默认路由或 DNS，不清空防火墙，不创建常驻 llmsm 服务。

### Windows 一键配置（WSL2 NAT）

随项目提供 [`scripts/windows-lan.ps1`](scripts/windows-lan.ps1)。在普通 Windows PowerShell 执行下面一行（发行版和端口改成清单中的值）：

```powershell
$p=Join-Path $env:TEMP ([guid]::NewGuid().ToString()+'.ps1'); try { Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/gdrencn/llm-service-manager/v0.10.0/scripts/windows-lan.ps1' -OutFile $p -ErrorAction Stop; & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -Distro 'llm' -Port 55555; if ($LASTEXITCODE -ne 0) { throw 'LAN setup failed' } } finally { Remove-Item -LiteralPath $p -ErrorAction SilentlyContinue }
```

脚本自动申请 UAC 提权，依次说明每步的目的、原生命令及结果；UAC 仍需用户确认。
使用 Windows 自带的 PowerShell 5.1、WSL、IP Helper、`netsh interface portproxy` 和 NetSecurity。
先检查 NAT、Linux 服务可达性、专用网络、端口占用/保留范围，再配置对应转发和具名防火墙规则。
仅开放 Private + LocalSubnet，不修改其它规则或网络类别；未设置为专用网络时提示用户先确认可信网络。
普通用户若通过另一个管理员账号提权，该账号可能无法访问原用户的 WSL 发行版，脚本会失败并停止。

可下载脚本后加 `-CheckOnly` 查看检查结果与拟执行操作，不写入网络配置（仍可能请求 UAC 来完成只读检查）。
执行失败即停止；已完成的原生配置保留，可修复原因后重跑。不会建立计划任务、后台进程，也不会修改机器级执行策略。
WSL IP 变化后重跑即可更新原生转发。工具/脚本删除不影响已写入的配置，但 Windows 重启后仍需先启动 WSL 和模型服务。

Linux 自动配置仅使用发行版包管理器，不运行底座安装器或下载模型。对任意自定义规则无法同时保证自动放行和“其它不动”，因此该分支明确交给原规则管理员处理。

依据：[Ubuntu 防火墙](https://documentation.ubuntu.com/security/security-features/network/firewall/)、
[firewalld 开放端口](https://firewalld.org/documentation/howto/open-a-port-or-service)、
[PowerShell RunAs](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/start-process)。

## 发布渠道

- test：`v版本号` 标签，GitHub Release 标记 **Pre-release**，不标记 Latest。test 安装入口固定该标签；Windows 脚本也固定对应标签。
- stable：未来使用 `stable/版本号` 标签，采用已验收 test 的同一份产品与配对测试产物，标记正式 Release / Latest。未验收不晋级。本次没有 stable。
- 发布标签与资产不覆盖；后续修复使用新版本。源代码包包含该版本配对测试，发行资产提供 SHA-256 校验清单。
- 底座安装和模型下载不属于自动验收范围；安装命令只进行静态核对，测试配置、切换、停止和交互。

```sh
python3 -m unittest discover -s tests -q
```
