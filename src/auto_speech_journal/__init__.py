"""Auto Speech Journal."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("auto-speech-journal")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = ["__version__"]
