"""Unit tests for the Ephemeral Screen Auditor and guaranteed file deletion."""
import glob
import os
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from screen_auditor import ScreenAuditor, MultimodalScreenClassifier


def test_ephemeral_screenshot_deletion_on_success(tmp_path, monkeypatch):
    """Verify screenshot file is strictly deleted immediately after classification."""
    monkeypatch.chdir(tmp_path)

    fake_img = Image.new("RGB", (200, 200), color="blue")
    monkeypatch.setattr("screen_auditor.capture_desktop", lambda: fake_img)

    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = ("STUDYING_CODING", "Coding in IDE.")

    mock_feedback = MagicMock()

    auditor = ScreenAuditor(feedback_service=mock_feedback, classifier=mock_classifier)
    cat, obs = auditor.audit_once()

    assert cat == "STUDYING_CODING"
    assert obs == "Coding in IDE."
    mock_feedback.submit_expression.assert_called_once_with("screen_studying_coding")

    # Critical security check: No temp_audit_*.jpg files persist on disk
    remaining_temp_files = glob.glob(str(tmp_path / "temp_audit_*.jpg"))
    assert len(remaining_temp_files) == 0, f"Leaked temporary screenshot files: {remaining_temp_files}"


def test_ephemeral_screenshot_deletion_on_exception(tmp_path, monkeypatch):
    """Verify screenshot file is deleted even if classifier raises an exception in try block."""
    monkeypatch.chdir(tmp_path)

    fake_img = Image.new("RGB", (200, 200), color="red")
    monkeypatch.setattr("screen_auditor.capture_desktop", lambda: fake_img)

    mock_classifier = MagicMock()
    mock_classifier.classify.side_effect = RuntimeError("Multimodal API connection failed")

    auditor = ScreenAuditor(classifier=mock_classifier)

    with pytest.raises(RuntimeError, match="Multimodal API connection failed"):
        auditor.audit_once()

    # Verify screenshot was deleted despite exception
    remaining_temp_files = glob.glob(str(tmp_path / "temp_audit_*.jpg"))
    assert len(remaining_temp_files) == 0, f"Leaked temporary screenshot files on error: {remaining_temp_files}"


def test_idle_desktop_skips_feedback():
    """Verify IDLE_DESKTOP does not trigger spoken feedback."""
    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = ("IDLE_DESKTOP", "Empty desktop wallpaper.")

    mock_feedback = MagicMock()

    with patch("screen_auditor.capture_desktop", return_value=Image.new("RGB", (100, 100))):
        auditor = ScreenAuditor(feedback_service=mock_feedback, classifier=mock_classifier)
        cat, _ = auditor.audit_once()

    assert cat == "IDLE_DESKTOP"
    mock_feedback.submit_expression.assert_not_called()


def test_classifier_response_parsing():
    """Verify category and observation extraction from LLM response text."""
    classifier = MultimodalScreenClassifier()

    text1 = "CATEGORY: WATCHING_STREAM_OR_VIDEO\nOBSERVATION: User is watching YouTube video."
    cat1, obs1 = classifier._parse_response(text1)
    assert cat1 == "WATCHING_STREAM_OR_VIDEO"
    assert "watching YouTube" in obs1

    text2 = "User appears to be in GAMING mode playing a shooter game.\nOBSERVATION: Fullscreen gameplay active."
    cat2, obs2 = classifier._parse_response(text2)
    assert cat2 == "GAMING"
    assert "Fullscreen gameplay" in obs2


def test_auditor_lifecycle():
    """Verify start and stop event handling for background thread."""
    auditor = ScreenAuditor(interval_seconds=100.0)
    auditor.start()
    assert auditor._thread is not None and auditor._thread.is_alive()
    auditor.stop()
    assert auditor._thread is None
