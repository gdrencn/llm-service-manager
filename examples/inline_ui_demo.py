"""Run from the project root: python3 -m examples.inline_ui_demo.

For another project, copy only inline_ui/ and this file; no llmsm is required.
"""

from inline_ui import UI, Cancelled, rich


def main():
    name = "world"
    with UI() as ui:
        while True:
            try:
                choice = ui.choose(
                    "Inline UI Demo",
                    [
                        ("name", rich(("Name  ", "key"), (name, "value"))),
                        ("hello", "Show greeting"),
                        ("exit", "Exit"),
                    ],
                )
                if choice == "exit":
                    return
                if choice == "name":
                    name = ui.input("Name", name)
                else:
                    ui.external(lambda: print("Hello, " + name), "Greeting")
            except Cancelled:
                continue
            except (EOFError, KeyboardInterrupt):
                return


if __name__ == "__main__":
    main()
