"""Fix sys.argv before app.py's argparse runs at module import time."""
import sys
# Replace pytest's collected argv with a clean app invocation so argparse doesn't
# see --tb, test file paths, etc. as unknown arguments.
sys.argv = ["app.py"]
