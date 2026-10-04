"""Build a complete, fail-closed static landing artifact for HTTPS hosting."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from landing.render import LEGAL_DOCUMENTS, render_legal_document, render_production_landing
from landing.site_config import (
    LandingConfigurationError,
    LandingPublicConfig,
    build_landing_public_config,
)
from storage.runtime_config import process_environment_settings


_LANDING_ROOT = Path(__file__).parent
_ASSET_DIRECTORY = _LANDING_ROOT / "assets"
_STATIC_FILES = ("styles.css", "app.js", "legal.css")


class LandingBuildError(RuntimeError):
    """The requested local output location is not safe for a static build."""


def build_production_artifact(
    config: LandingPublicConfig,
    output_directory: str | Path,
) -> Path:
    """Write an upload-ready static artifact only after production validation."""
    if not config.seller_disclosure_address:
        raise ValueError("Legal publication requires SELLER_DISCLOSURE_ADDRESS.")
    if not config.production_ready:
        raise LandingBuildError("Production landing configuration is incomplete.")
    # Check before creating output/staging: the new offer must not silently
    # omit mandatory seller disclosure. Public values are never echoed.
    legal_pages = {
        name: render_legal_document(name, config) for name in LEGAL_DOCUMENTS
    }

    output = Path(output_directory).expanduser().resolve()
    if output.exists():
        raise LandingBuildError(
            "Landing artifact output directory must not already exist."
        )
    if _LANDING_ROOT.resolve() in output.parents:
        raise LandingBuildError(
            "Landing artifact output must be outside the source directory."
        )
    if not output.parent.is_dir():
        raise LandingBuildError(
            "Landing artifact output parent directory must already exist."
        )

    staging = Path(tempfile.mkdtemp(prefix=".landing-build-", dir=output.parent))
    try:
        (staging / "index.html").write_text(
            render_production_landing(config),
            encoding="utf-8",
        )
        for filename, page in legal_pages.items():
            (staging / filename).write_text(page, encoding="utf-8")
        for filename in _STATIC_FILES:
            shutil.copy2(_LANDING_ROOT / filename, staging / filename)
        shutil.copytree(_ASSET_DIRECTORY, staging / "assets")
        staging.replace(output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a production static landing from process environment only."
    )
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        config = build_landing_public_config(
            process_environment_settings().optional,
            production=True,
        )
        output = build_production_artifact(config, arguments.output)
    except (LandingBuildError, LandingConfigurationError, ValueError) as error:
        print(str(error))
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
