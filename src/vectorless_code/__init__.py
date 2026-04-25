"""vectorless-code — code search engine built on vectorless."""

__version__ = "0.1.0"


def main() -> None:
    """Entry point for the ``vectorless-code`` command."""
    from vectorless_code.cli import app

    app()
