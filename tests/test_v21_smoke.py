"""Run locally with pytest; uses temporary SQLite, no PostgreSQL required."""
import os
import subprocess
import sys
from pathlib import Path

def test_v21_smoke():
    script=Path(__file__).with_name('smoke_runner.py')
    result=subprocess.run([sys.executable,str(script)],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
