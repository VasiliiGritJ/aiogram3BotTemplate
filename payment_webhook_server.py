"""Explicit entrypoint for the selected payment provider HTTP role."""

from __future__ import annotations

import argparse

from aiohttp import web

from storage.payment_webhook_http import build_deployed_payment_webhook_runtime
from storage.runtime_config import (
    ApplicationEnvironment,
    local_development_settings,
    process_environment_settings,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the payment webhook role.")
    parser.add_argument(
        "--local-env",
        action="store_true",
        help="Explicitly load storage/.env for local test-mode development.",
    )
    arguments = parser.parse_args(argv)
    if arguments.local_env:
        settings = local_development_settings()
        environment = ApplicationEnvironment.DEVELOPMENT
    else:
        settings = process_environment_settings()
        environment = ApplicationEnvironment.PRODUCTION
    runtime = build_deployed_payment_webhook_runtime(
        settings,
        expected_environment=environment,
    )
    web.run_app(
        runtime.app,
        host=runtime.config.bind_host,
        port=runtime.config.bind_port,
        shutdown_timeout=10,
        # Auth codes and signed checkout/callback query strings are not log data.
        # Application handlers emit bounded, normalized observability events.
        access_log=None,
    )


if __name__ == "__main__":
    main()
