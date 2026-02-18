"""Tests for workrecap.logging_config."""

import logging
import sys

from workrecap.logging_config import NOISY_LOGGERS, reset_logging, setup_logging


class TestSetupLogging:
    def teardown_method(self):
        reset_logging()

    def test_default_level_is_info(self):
        setup_logging()
        root = logging.getLogger("workrecap")
        assert root.level == logging.INFO

    def test_debug_level(self):
        setup_logging(level=logging.DEBUG)
        root = logging.getLogger("workrecap")
        assert root.level == logging.DEBUG

    def test_outputs_to_stderr(self):
        setup_logging()
        root = logging.getLogger("workrecap")
        assert len(root.handlers) == 1
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stderr

    def test_idempotent(self):
        setup_logging()
        setup_logging()
        setup_logging()
        root = logging.getLogger("workrecap")
        assert len(root.handlers) == 1

    def test_silences_noisy_loggers(self):
        setup_logging()
        for name in NOISY_LOGGERS:
            assert logging.getLogger(name).level == logging.WARNING


class TestResetLogging:
    def test_reset_clears_handlers(self):
        setup_logging()
        root = logging.getLogger("workrecap")
        assert len(root.handlers) == 1
        reset_logging()
        assert len(root.handlers) == 0

    def test_reset_allows_reconfigure(self):
        setup_logging(level=logging.INFO)
        reset_logging()
        setup_logging(level=logging.DEBUG)
        root = logging.getLogger("workrecap")
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1
