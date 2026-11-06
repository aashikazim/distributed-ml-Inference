import httpx

from infer_platform.common.sse import sse_data


async def test_sse_multiline_crlf_and_comments() -> None:
    response = httpx.Response(
        200, text=': ping\r\ndata: {"a":\r\ndata: 1}\r\n\r\ndata: [DONE]\r\n\r\n'
    )
    assert [data async for data in sse_data(response)] == ['{"a":\n1}', "[DONE]"]


async def test_sse_bounds_include_empty_data_lines() -> None:
    import pytest

    for text in ("data: " + "x" * 100, "data:\n" * 100):
        with pytest.raises(ValueError):
            _ = [
                event
                async for event in sse_data(httpx.Response(200, text=text), max_event_chars=32)
            ]
