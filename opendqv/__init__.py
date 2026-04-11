"""OpenDQV Core — contract-driven data quality validation engine."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("opendqv")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
