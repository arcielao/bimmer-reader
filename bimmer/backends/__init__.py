"""Backends share one interface: connect(), disconnect(), connected, label and
job(ecu, job, params="", raw=False) -> (system_set, result_sets)."""

from bimmer.backends.demo import DemoBackend
from bimmer.backends.ediabas import EdiabasBackend

__all__ = ["DemoBackend", "EdiabasBackend"]
