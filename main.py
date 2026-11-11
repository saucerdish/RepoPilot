"""Compatibility entry point for the RepoPilot terminal client."""

from repopilot.factory import build_agent


def main():
    from repopilot.cli.app import main as cli_main
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
