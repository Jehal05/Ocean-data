"""Parsers that turn source files into the SCATS common data model (v0.1)."""
from .base import (ParseError, Parser, SourceMapping, __version__, detect, parse_file, parsers,  # noqa: F401
                   register)
from . import argo_netcdf, csv_sources, grid_netcdf  # noqa: F401  (importing registers the parsers)
