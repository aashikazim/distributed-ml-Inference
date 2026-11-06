import httpx

from infer_platform.common.sse import sse_data


async def test_sse_multiline_crlf_and_comments() -> None:
    response = httpx.Response(
        200, text=': ping\r\ndata: {"a":\r\ndata: 1}\r\n\r\ndata: [DONE]\r\n\r\n'
    )
    assert [data async for data in sse_data(response)] == ['{"a":\n1}', "[DONE]"]
