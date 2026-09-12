import os
import sys
from pathlib import Path

current_file_path = Path(__file__).resolve()
current_dir = current_file_path.parent
JS_SCRIPT_PATH = current_dir / 'javascript'

execute_dir = os.path.split(os.path.realpath(sys.argv[0]))[0]
