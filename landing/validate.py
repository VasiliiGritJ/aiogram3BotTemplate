"""Validate only public production-landing values before an approved publish."""

from __future__ import annotations

import argparse

from landing.site_config import LandingConfigurationError, build_landing_public_config
from storage.runtime_config import process_environment_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate public landing configuration.")
    parser.add_argument("--production", action="store_true")
    arguments = parser.parse_args()
    settings = process_environment_settings()
    try:
        build_landing_public_config(settings.optional, production=arguments.production)
    except LandingConfigurationError as error:
        print(str(error))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
