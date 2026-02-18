"""File logging tests."""

import logging

from workrecap.logging_config import setup_file_logging, reset_logging


class TestFileLogging:
    def setup_method(self):
        reset_logging()

    def teardown_method(self):
        reset_logging()

    def test_creates_log_file(self, tmp_path):
        log_dir = tmp_path / ".log"
        handler = setup_file_logging(log_dir)
        assert log_dir.exists()
        assert handler is not None

        # Write a log message
        logger = logging.getLogger("workrecap.test_file")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.info("test message")
        handler.flush()

        # Verify log file exists with content
        log_files = list(log_dir.glob("*.log"))
        assert len(log_files) == 1
        content = log_files[0].read_text()
        assert "test message" in content

        handler.close()

    def test_log_file_name_format(self, tmp_path):
        log_dir = tmp_path / ".log"
        handler = setup_file_logging(log_dir)

        log_files = list(log_dir.glob("*.log"))
        assert len(log_files) == 1
        # Format: YYYYMMDD_HHMMSS.log
        name = log_files[0].stem
        assert len(name) == 15  # 8 + 1 + 6
        assert name[8] == "_"

        handler.close()

    def test_debug_level_captured(self, tmp_path):
        log_dir = tmp_path / ".log"
        handler = setup_file_logging(log_dir)

        logger = logging.getLogger("workrecap.test_debug")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.debug("debug detail")
        handler.flush()

        log_files = list(log_dir.glob("*.log"))
        content = log_files[0].read_text()
        assert "debug detail" in content

        handler.close()

    def test_idempotent_dir_creation(self, tmp_path):
        log_dir = tmp_path / ".log"
        log_dir.mkdir()
        handler = setup_file_logging(log_dir)
        assert handler is not None
        handler.close()
