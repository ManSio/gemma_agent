"""SSE parse for OpenRouter stream."""

from core.openrouter_stream import merge_stream_finish_reason, parse_openrouter_sse_data_line


def test_parse_delta_content():
    line = 'data: {"choices":[{"delta":{"content":"Привет"}}]}'
    assert parse_openrouter_sse_data_line(line) == "Привет"


def test_parse_done_and_empty():
    assert parse_openrouter_sse_data_line("data: [DONE]") is None
    assert parse_openrouter_sse_data_line("") is None


def test_finish_reason():
    line = 'data: {"choices":[{"finish_reason":"stop","delta":{}}]}'
    assert merge_stream_finish_reason(line) == "stop"
