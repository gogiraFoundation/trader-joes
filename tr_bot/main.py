"""Trading loop entrypoint — logic lives in `tr_bot.engine`."""

from __future__ import annotations

from tr_bot.engine import run_forever


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
