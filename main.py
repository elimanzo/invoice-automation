"""Entry point.

The brief specifies `python main.py --invoice_path=...`, so this file exists at the repo
root and does nothing but delegate.
"""

import sys

from invoice_automation.cli import main

if __name__ == "__main__":
    sys.exit(main())
