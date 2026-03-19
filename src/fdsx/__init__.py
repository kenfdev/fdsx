"""fdsx - Declarative AI agent workflow execution framework."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("fdsx")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
