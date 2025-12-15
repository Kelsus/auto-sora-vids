"""Tests for the _resolve_bundle_path helper in PipelineOrchestrator."""

from pathlib import Path
import tempfile

from aivideomaker.orchestrator import PipelineOrchestrator


def test_resolve_bundle_path_returns_none_for_none():
    """Should return None when input is None."""
    result = PipelineOrchestrator._resolve_bundle_path(None, Path("/tmp/run"))
    assert result is None


def test_resolve_bundle_path_returns_existing_path():
    """Should return the original path if it exists."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        existing_path = Path(f.name)
        result = PipelineOrchestrator._resolve_bundle_path(existing_path, Path("/tmp/run"))
        assert result == existing_path
        existing_path.unlink()


def test_resolve_bundle_path_resolves_media_paths():
    """Should relocate media/ paths to the current run directory."""
    with tempfile.TemporaryDirectory() as run_dir:
        run_path = Path(run_dir)
        # Create a narration file in the current run directory
        voice_dir = run_path / "media" / "voice" / "test_voice"
        voice_dir.mkdir(parents=True)
        narration_file = voice_dir / "narration.mp3"
        narration_file.touch()

        # Simulate a stored absolute path from a different run directory
        stored_path = Path("/tmp/data/old-slug/media/voice/test_voice/narration.mp3")

        result = PipelineOrchestrator._resolve_bundle_path(stored_path, run_path)
        assert result == narration_file


def test_resolve_bundle_path_resolves_exports_paths():
    """Should relocate exports/ paths to the current run directory."""
    with tempfile.TemporaryDirectory() as run_dir:
        run_path = Path(run_dir)
        # Create a caption file in the current run directory
        exports_dir = run_path / "exports"
        exports_dir.mkdir(parents=True)
        caption_file = exports_dir / "captions.ass"
        caption_file.touch()

        # Simulate a stored absolute path from a different run directory
        stored_path = Path("/tmp/data/old-slug/exports/captions.ass")

        result = PipelineOrchestrator._resolve_bundle_path(stored_path, run_path)
        assert result == caption_file


def test_resolve_bundle_path_returns_original_when_not_found():
    """Should return the original path if the relocated file doesn't exist."""
    with tempfile.TemporaryDirectory() as run_dir:
        run_path = Path(run_dir)
        # Don't create any file in the run directory
        stored_path = Path("/tmp/data/old-slug/media/voice/test_voice/narration.mp3")

        result = PipelineOrchestrator._resolve_bundle_path(stored_path, run_path)
        assert result == stored_path


def test_resolve_bundle_path_handles_string_input():
    """Should accept string paths as well as Path objects."""
    with tempfile.TemporaryDirectory() as run_dir:
        run_path = Path(run_dir)
        voice_dir = run_path / "media" / "voice" / "test_voice"
        voice_dir.mkdir(parents=True)
        narration_file = voice_dir / "narration.mp3"
        narration_file.touch()

        # Pass as string
        stored_path = "/tmp/data/old-slug/media/voice/test_voice/narration.mp3"

        result = PipelineOrchestrator._resolve_bundle_path(stored_path, run_path)
        assert result == narration_file
