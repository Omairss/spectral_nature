from __future__ import annotations

from services.runtime_policy import presentation_layer_only_enabled, section_data_available


def test_presentation_layer_only_requires_explicit_opt_in():
    assert presentation_layer_only_enabled(None) is False
    assert presentation_layer_only_enabled("") is False
    assert presentation_layer_only_enabled("true") is True
    assert presentation_layer_only_enabled("1") is True
    assert presentation_layer_only_enabled("false") is False


def test_section_data_available_requires_live_api_for_live_only_sections():
    assert section_data_available(
        api_available=True,
        pipeline_available=True,
        presentation_only=False,
        allow_pipeline=False,
    ) is True
    assert section_data_available(
        api_available=False,
        pipeline_available=True,
        presentation_only=False,
        allow_pipeline=False,
    ) is False


def test_section_data_available_allows_pipeline_for_snapshot_capable_sections():
    assert section_data_available(
        api_available=False,
        pipeline_available=True,
        presentation_only=False,
        allow_pipeline=True,
    ) is True
    assert section_data_available(
        api_available=False,
        pipeline_available=True,
        presentation_only=True,
        allow_pipeline=True,
    ) is True


def test_section_data_available_blocks_live_only_sections_in_presentation_mode():
    assert section_data_available(
        api_available=True,
        pipeline_available=True,
        presentation_only=True,
        allow_pipeline=False,
    ) is False
    assert section_data_available(
        api_available=False,
        pipeline_available=False,
        presentation_only=True,
        allow_pipeline=False,
    ) is False
