"""Shared configuration for the unit test suite.

``python -m unittest discover -s tests -t .`` imports this package before any test
module, which is the earliest point where logging can be configured. Library
level ``logger.warning`` and ``logger.exception`` calls (for example the worker
ingestion traceback on a deliberately invalid document) otherwise fall through
to the interpreter's last-resort handler and interleave with the unittest
progress output.

``assertLogs`` is unaffected: it attaches its own handler to the logger under
test and sets that logger's level explicitly.
"""

import logging

logging.getLogger().addHandler(logging.NullHandler())
logging.getLogger().setLevel(logging.CRITICAL)
