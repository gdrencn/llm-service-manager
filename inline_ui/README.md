# Inline UI 交互规范与复用接口

`inline_ui` 是独立的 Python 行内终端 UI 包。仅使用标准库，当前支持 Python 3.10+、Linux/POSIX 终端。
它不导入 llmsm，不读写产品配置，不管理服务，不执行命令，也不保存语言偏好。
产品提供文字、可用状态和操作回调；UI 负责输入、排版、刷新、选择与终端恢复。

## 在其它项目中使用

复制整个 `inline_ui/` 包到新项目，或随新项目一起打包。无需复制 `llm_service_manager/`。
目前随 llm-service-manager 源码分发，尚未单独发布到 PyPI，不要执行不存在的 pip 安装命令。

```python
from inline_ui import UI, Cancelled

with UI(translate=lambda zh, en: zh) as ui:
    while True:
        try:
            key = ui.choose("我的工具", [("show", "查看状态"), ("exit", "退出")])
            if key == "exit":
                break
            ui.external(lambda: print("这里调用你的业务函数"), "当前状态")
        except Cancelled:
            continue
        except (EOFError, KeyboardInterrupt):
            break
```

完整可运行示例：仓库根目录执行 `python3 -m examples.inline_ui_demo`。
这段示例不读取系统配置，不执行安装或服务操作。

## 接口约定

| 接口 | 输入与行为 |
| --- | --- |
| `UI(translate=None)` | 注入 `(中文, English) -> str` 翻译函数；默认中文。函数可读取应用当前语言，UI 不保存全局语言。 |
| `with UI() as ui` | 退出时清除本对象绘制的临时菜单；键盘上下文负责恢复终端属性和光标。 |
| `choose(title, rows, details=(), disabled=(), focus=0)` | `rows` 是 `(唯一 key, 标签)` 列表；标签支持 `Styled`。返回选中 key，不执行操作。`disabled` 包含不可选择的 key；`focus` 只决定默认焦点，不改变顺序。 |
| `blocks(title, rows, build, *, details=(), footer=(), disabled=(), focus=0, refresh=None, min_width=40)` | 多行清单。`build(width, focus)` 返回与 rows 一一对应的非空行列表。返回选中 key；按 R 返回 `refresh`（非 None 时启用），由应用重新获取数据。不执行刷新或安装。 |
| `input(title, default="")` | 返回字符串。业务层解析数字、校验范围或保存。 |
| `loading(title, work)` | 立即显示检测提示，在后台调用 `work(cancel_event)`；Esc/EOF/Ctrl+C 设置取消事件并恢复终端。回调必须配合取消且不得写终端；完成值返回，异常传播。 |
| `document(title, lines, actions, *, focus=0)` | 完整输出所有内容到终端历史，不按终端高度裁剪；底部显示操作菜单。长命令保留原始行，由终端自动折行。 |
| `back()` | 显示单项“返回”菜单；Enter 或 Esc 返回，普通按键不回显。菜单结束后清理临时区域。 |
| `text(title, body)` | 保留文字输出，显示统一的“返回”菜单项。 |
| `external(callback, title=None)` | 清除临时菜单，打印分隔标题，调用业务回调，成功后显示统一的“返回”菜单项。回调异常原样向应用传播；不重试、不吞错。 |
| `clear()` / `finish()` | 只清除本 UI 绘制的临时区域，保留之前输出。 |
| `rich((文字, 样式), ...)` / `render(...)` | 构造语义样式、根据终端能力着色并按字符显示宽度裁剪。 |

`Cancelled` 表示 Esc 或显式取消，`EOFError` 表示输入结束，`KeyboardInterrupt` 表示 Ctrl+C。
应用决定这些事件意味着返回上层还是退出，不把取消作为业务执行成功。
`rows` 的 key 必须唯一，刷新返回值应与业务 key 区分。一个终端同时只使用一个活动 UI 实例。
回调执行时终端已恢复正常输入模式，可交给官方命令直接输出；UI 不捕获、复制或解析命令输出。

多行清单的构建函数只负责展示，不应查询服务或执行命令。数据由应用提前取得；按 R 返回后重取。
`blocks` 在普通输入模式下使用 rows 的文字标签，因此标签必须包含足够的内容，不应只有编号。

## 视觉和操作规范

- 保持行内刷新，不进入全屏备用缓冲区，不清空 shell 历史。
- 产品页标题使用青色 `title`；正文段落标题使用粉色 `section`，不能冒用页标题色。
- 粉色 `accent` / `command` 为 `#F38BA8`；成功绿色 `good` 为 `#A6E3A1`；链接蓝色 `link` 为 `#89B4FA`。
- 普通标题与步骤保持白色；分组名可用加粗白色 `group`。参数名 `key`、值 `value`、说明 `muted` 区分层次。
- 菜单与按钮的选中项统一使用 `highlight` 背景；输入文字使用终端默认样式，插入位置使用终端原生光标，不设置颜色、形状或闪烁方式，不给已输入文字添加背景。不可操作项可以获得焦点供阅读，但 Enter 不触发业务。
- ↑↓ 或 j/k 移动，Enter 选择，Esc 返回，Ctrl+C 中断；文本输入支持左右移动、Home/End、Delete/退格和 Ctrl+U 清空。
- 返回、取消、退出放在最后。安全默认项通过 focus 指定，不把默认项搬到首位。
- 保留输出的分隔线上下各一个空行；内容输出前先清除临时菜单，禁止重复打印同一命令输出。
- 菜单中隐藏光标；离开键盘输入上下文时恢复，包括异常和取消。不可捕获的 SIGKILL 无法保证恢复。
- 菜单在终端过矮、无法显示选项时不允许确认；多行项不能完整显示时同样禁止确认。提示调整终端，允许 Esc 返回。
- 非 TTY 或 TERM=dumb 使用编号输入；NO_COLOR 禁用颜色，不禁用 TTY 的行内菜单控制。

`style.py` 是共享视觉规范的唯一实现；`keyboard.py` 独立解码终端按键；`controls.py` 负责控件与布局。
业务专属内容（例如“安装全部项”、服务参数和文件路径）只能存在于应用层。
llmsm 的 `install_guide.py` 展示如何把专属清单绑定到通用 `blocks` 控件。

## 测试边界

框架测试覆盖独立复制导入、真实 PTY 键盘输入、菜单区域刷新、光标/终端属性恢复、
禁用项、刷新返回值、窄终端、纯文字回退和业务回调异常。产品测试单独验证业务绑定。
不支持并发 UI 实例、Windows 原生控制台，也不宣称支持所有 emoji 组合的精确列宽。
