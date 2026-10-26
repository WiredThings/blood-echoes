#!/usr/bin/env python3
"""Configure native sources using explicit fresh renderer/build inputs."""
from pathlib import Path
import sys
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent/'tools'))
from configure_native_build import main
if __name__=='__main__':main()
