# pipeline/__init__.py
# Marks the pipeline directory as a Python package.
# Exports the three pipeline stages for easy import.

from pipeline.extract  import extract_document   # Stage 1: raw text -> structured JSON
from pipeline.validate import validate_items      # Stage 2: clean + flag needs_review
from pipeline.load     import load_to_db          # Stage 3: write to SQLite

__all__ = ["extract_document", "validate_items", "load_to_db"]
