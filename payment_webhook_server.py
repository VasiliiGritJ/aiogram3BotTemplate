"""Separate production entrypoint for the YooKassa webhook HTTP process.

It intentionally reads only the process environment.  It does not load the
local ``storage/.env`` file and does not import or start the Telegram bot.
"""

from aiohttp import web
from environs import Env

from storage.payment_webhook_http import build_payment_webhook_http_runtime


def main() -> None:
    env = Env()
    runtime = build_payment_webhook_http_runtime(env)
    web.run_app(
        runtime.app,
        host=runtime.config.bind_host,
        port=runtime.config.bind_port,
    )


if __name__ == "__main__":
    main()
