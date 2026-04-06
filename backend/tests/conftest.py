"""
Shared test fixtures for the TailorTex backend test suite.

The most important fixture here is `mock_api_calls`, which prevents real API
calls and subprocess invocations in all server-level tests. It patches
`api.server.build_prompts` and `api.server.get_provider` with fast-completing
mocks so that background worker threads finish promptly and never crash due to
the `jobs` dict being cleared between tests.

Flow tests (test_full_generate_flow_*) stack their own `patch` calls on top of
this fixture — Python's mock stacking means the innermost patch wins within a
`with` block, so flow tests see their own tailored mock.
"""

import os
import sys

import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def mock_api_calls():
    """
    Auto-applied to every test in the suite.

    For tests outside test_server.py the patches target names that don't exist
    in those modules, so they are harmless no-ops.
    """
    from core.providers.base import GenerationResult
    from core.prompt_pipeline import PipelineOutput

    pipeline_out = PipelineOutput(
        system_prompt="sys",
        user_prompt="usr",
        preamble=r"\documentclass{article}",
        raw_job_description="jd",
    )

    mock_provider = MagicMock()
    mock_provider.generate.return_value = GenerationResult(
        tex_path=os.devnull,
        pdf_path=os.devnull,
    )

    with patch("api.server.build_prompts", return_value=pipeline_out), \
         patch("api.server.get_provider", return_value=mock_provider), \
         patch("api.server.os.startfile", create=True):
        yield
