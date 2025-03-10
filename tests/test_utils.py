import aiohttp
import pytest
from aioresponses import aioresponses

from pydoll import exceptions
from pydoll.utils import decode_image_to_bytes, get_browser_ws_address


class TestUtils:
    """
    Test class for the utility functions of the pydoll.utils module.
    Groups tests related to image decoding and communication with the browser.
    """

    def test_decode_image_to_bytes(self):
        """
        Tests the decode_image_to_bytes function.
        Checks if the function can correctly decode a base64 string into its
        original bytes
        """
        base64code = 'aGVsbG8gd29ybGQ='  # 'hello world' em base64
        assert decode_image_to_bytes(base64code) == b'hello world'

    @pytest.mark.asyncio
    async def test_successful_response(self):
        """
        Tests the success scenario of obtaining the browser's WebSocket address.
        Checks if the function correctly returns the WebSocket URL when the API
        response contains the expected field.
        """
        port = 9222
        expected_url = 'ws://localhost:9222/devtools/browser/abc123'

        with aioresponses() as mocked:
            mocked.get(
                f'http://localhost:{port}/json/version',
                payload={'webSocketDebuggerUrl': expected_url},
            )
            result = await get_browser_ws_address(port)
            assert result == expected_url

    @pytest.mark.asyncio
    async def test_network_error(self):
        """
        Tests the function's behavior when a network error occurs.
        Checks if the function raises the appropriate NetworkError exception
        when communication with the browser fails.
        """
        port = 9222

        with pytest.raises(exceptions.NetworkError):
            with aioresponses() as mocked:
                mocked.get(
                    f'http://localhost:{port}/json/version',
                    exception=aiohttp.ClientError,
                )
                await get_browser_ws_address(port)

    @pytest.mark.asyncio
    async def test_missing_websocket_url(self):
        """
        Tests the behavior when the API response does not contain the WebSocket
        URL.
        Checks if the function raises the InvalidResponse exception when the
        'webSocketDebuggerUrl' field is missing from the response.
            """
        port = 9222

        with aioresponses() as mocked:
            mocked.get(
                f'http://localhost:{port}/json/version',
                payload={'someOtherKey': 'value'},
            )
            with pytest.raises(exceptions.InvalidResponse):
                await get_browser_ws_address(port)
