"""Shared fixtures for pytest-based tests.

The unittest-style tests in test_predictor.py do not use this file; only the
HA integration tests (test_coordinator.py, test_config_flow.py) rely on it.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/spotoracle into every HA test."""
    yield
