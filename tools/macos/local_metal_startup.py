#!/usr/bin/env python3
"""Source-checkout entry for the isolated native runtime's local Metal preparation.

The prepared local component stage supplies this entry from its admitted runtime
tools; the ordinary Rosetta/production launch path does not invoke it.
"""
from pathlib import Path
import runpy, sys

runtime_tools = Path(__file__).resolve().parents[2]/'experiments/arm64-runtime/tools'
sys.path.insert(0, str(runtime_tools))
runpy.run_path(str(runtime_tools/'launcher_local_aot.py'), run_name='__main__')
