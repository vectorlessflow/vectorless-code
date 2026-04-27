"""vectorless-code — Code search and navigation engine built on vectorless."""

import logging

logging.basicConfig(level=logging.INFO)

from vectorless_code._version import __version__
from vectorless_code.server import main

__all__ = ["main", "__version__"]
