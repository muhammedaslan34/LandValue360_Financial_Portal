"""Compatibility version namespace. Legacy finance equations were removed in v2.1."""
from landvalue360_common.versions import FINANCE_MODEL_VERSION
__version__ = FINANCE_MODEL_VERSION
__all__ = ["FINANCE_MODEL_VERSION", "__version__"]
