"""Real car: EDIABAS api32.dll through pydiabas."""

from __future__ import annotations

import os
import struct
import sys

from bimmer.diag import DiagError, clean_value


class EdiabasBackend:
    """Real car: EDIABAS api32.dll through pydiabas."""

    label = "EDIABAS"

    def __init__(self, ediabas_dir: str):
        self.ediabas_dir = ediabas_dir
        self._pd = None

    def connect(self):
        if sys.platform != "win32":
            raise DiagError("EDIABAS only runs on Windows. Use --demo to try the app here.")
        if struct.calcsize("P") * 8 != 32:
            raise DiagError(
                "This is 64-bit Python. EDIABAS needs 32-bit Python - install the "
                "'Windows installer (32-bit)' from python.org and run the app with it."
            )
        bin_dir = os.path.join(self.ediabas_dir, "bin")
        if not os.path.isfile(os.path.join(bin_dir, "api32.dll")):
            raise DiagError(f"api32.dll not found in {bin_dir}. Check the EDIABAS folder in Settings.")
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(bin_dir)
        except Exception:
            pass
        try:
            from pydiabas import PyDIABAS  # loads api32.dll on import
        except ImportError:
            raise DiagError("pydiabas isn't installed. Run:  py -3-32 -m pip install pydiabas")
        except Exception as e:
            raise DiagError(f"Couldn't load api32.dll: {e}")
        try:
            self._pd = PyDIABAS()
            self._pd.start()
        except Exception as e:
            self._pd = None
            raise DiagError(f"EDIABAS failed to start: {e}")

    def disconnect(self):
        if self._pd is not None:
            try:
                self._pd.end()
            finally:
                self._pd = None

    @property
    def connected(self):
        return self._pd is not None

    def job(self, ecu: str, job: str, params: str | bytes = "", raw: bool = False) -> tuple[dict, list[dict]]:
        """Run a job. bytes params go to EDIABAS as one binary argument; raw=True skips clean_value."""
        if self._pd is None:
            raise DiagError("Not connected.")
        try:
            result = self._pd.job(ecu, job, parameters=params)
        except Exception as e:
            raise DiagError(str(e) or e.__class__.__name__)
        conv = (lambda v: v) if raw else clean_value
        dicts = [{k: conv(v) for k, v in d.items()} for d in result.as_dicts()]
        if not dicts:
            return {}, []
        return dicts[0], dicts[1:]
