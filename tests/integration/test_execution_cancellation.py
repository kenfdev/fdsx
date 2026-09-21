"""Cancellation at subprocess creation and auxiliary extraction boundaries."""

import signal
import subprocess
from unittest.mock import Mock

import pytest

from fdsx.core.cancellation import (
    Cancellation,
    ExecutionInterrupted,
    current_cancellation,
)
from fdsx.core.engine.signals import SignalHandler
from fdsx.core.extraction import extract_value
from fdsx.core.extraction_fallback import ResolvedFallback
from fdsx.models.flow import ExtractionFallback, ExtractRule
from fdsx.providers.base import _run_subprocess


def test_process_launched_during_interrupt_is_stopped_on_registration(monkeypatch):
    handler = SignalHandler(None, "test")
    original = subprocess.Popen
    processes = []

    def launch(*args, **kwargs):
        proc = original(*args, **kwargs)
        processes.append(proc)
        # Signal processing took its snapshot before this process registered.
        handler._interrupted = True
        handler._signum = signal.SIGTERM
        handler.cancellation.stopped.set()
        return proc

    monkeypatch.setattr(subprocess, "Popen", launch)
    token = current_cancellation.set(handler.cancellation)
    try:
        result = _run_subprocess(["sleep", "20"], timeout=2)
        assert result.exit_code != 0
        assert len(processes) == 1
        assert processes[0].poll() is not None
    finally:
        current_cancellation.reset(token)
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)


@pytest.mark.parametrize("kind", ["rule", "workflow"])
def test_fallback_inherits_registration_and_honors_cancellation(kind):
    registered = []
    cancellation = Cancellation(registered.append)
    token = current_cancellation.set(cancellation)
    provider = Mock()
    provider.execute.side_effect = lambda **_: _run_subprocess(
        ["printf", "APPROVED"], timeout=2
    )
    rule = ExtractRule(
        strategy=["keyword"],
        pattern="APPROVED",
        result_path="$.value",
        **(
            {"fallback": {"provider": "claude", "model": "fake", "prompt": "Classify"}}
            if kind == "rule"
            else {}
        ),
    )
    resolved = (
        ResolvedFallback(
            ExtractionFallback(provider="claude", model="fake"), "workflow"
        )
        if kind == "workflow"
        else None
    )
    try:
        assert (
            extract_value(
                "unclassified",
                rule,
                lambda _: provider,
                source_provider="claude",
                resolved_fallback=resolved,
            )
            == "APPROVED"
        )
        assert len(registered) == 1
        cancellation.stopped.set()
        with pytest.raises(ExecutionInterrupted):
            extract_value(
                "unclassified",
                rule,
                lambda _: provider,
                source_provider="claude",
                resolved_fallback=resolved,
            )
        assert provider.execute.call_count == 1
    finally:
        current_cancellation.reset(token)


@pytest.mark.parametrize("kind", ["rule", "workflow"])
def test_unexpected_fallback_exception_is_not_an_extraction_miss(kind):
    provider = Mock()
    provider.execute.side_effect = RuntimeError("broken execution")
    rule = ExtractRule(
        strategy=["keyword"],
        pattern="APPROVED",
        result_path="$.value",
        **(
            {"fallback": {"provider": "claude", "model": "fake", "prompt": "Classify"}}
            if kind == "rule"
            else {}
        ),
    )
    resolved = (
        ResolvedFallback(
            ExtractionFallback(provider="claude", model="fake"), "workflow"
        )
        if kind == "workflow"
        else None
    )
    with pytest.raises(RuntimeError, match="broken execution"):
        extract_value(
            "unclassified",
            rule,
            lambda _: provider,
            source_provider="claude",
            resolved_fallback=resolved,
        )
