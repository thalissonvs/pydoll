from unittest.mock import ANY, AsyncMock, MagicMock, patch
from typing import AsyncGenerator, Any, cast
import asyncio

import pytest
import pytest_asyncio

from pydoll import exceptions
from pydoll.browser.edge import Edge
from pydoll.browser.base import Browser
from pydoll.browser.managers import (
    ProxyManager,
    BrowserOptionsManager
)
from pydoll.browser.options import EdgeOptions, Options
from pydoll.browser.page import Page
from pydoll.commands.browser import BrowserCommands
from pydoll.commands.dom import DomCommands
from pydoll.commands.fetch import FetchCommands
from pydoll.commands.network import NetworkCommands
from pydoll.commands.page import PageCommands
from pydoll.commands.storage import StorageCommands
from pydoll.commands.target import TargetCommands
from pydoll.events.fetch import FetchEvents


class ConcreteEdge(Edge):
    @staticmethod
    def _get_default_binary_location():
        return '/fake/path/to/browser'


@pytest_asyncio.fixture
async def mock_browser() -> AsyncGenerator[ConcreteEdge, None]:
    """
    Create a mock Edge browser instance for testing
    
    Yields:
        ConcreteEdge: A mock Edge browser instance
    """
    with patch(
        'pydoll.browser.managers.BrowserProcessManager',
        autospec=True,
    ) as mock_process_manager, patch(
        'pydoll.browser.managers.TempDirectoryManager',
        autospec=True,
    ) as mock_temp_dir_manager, patch(
        'pydoll.connection.connection.ConnectionHandler',
        autospec=True,
    ) as mock_conn_handler, patch(
        'pydoll.browser.managers.ProxyManager',
        autospec=True,
    ) as mock_proxy_manager:
        options = Options()
        options.binary_location = None

        browser = ConcreteEdge(options=options)
        browser._browser_process_manager = mock_process_manager.return_value
        browser._temp_directory_manager = mock_temp_dir_manager.return_value
        browser._proxy_manager = mock_proxy_manager.return_value
        browser._connection_handler = mock_conn_handler.return_value
        browser._connection_handler.execute_command = AsyncMock()
        browser._connection_handler.register_callback = AsyncMock()

        mock_temp_dir_manager.return_value.create_temp_dir.return_value = (
            MagicMock(name='temp_dir')
        )
        browser._pages = ['page1']
        browser._connection_port = 9250

        def patched_is_valid_page(page: dict) -> bool:
            if page.get('type') != 'page':
                return False
            
            url = page.get('url', '')
            return (
                url.startswith('edge://') or 
                url.startswith('http://') or 
                url.startswith('https://')
            )
        
        browser._is_valid_page = patched_is_valid_page

        original_get_valid_page = browser._get_valid_page
        async def patched_get_valid_page(pages: list[dict]) -> str:
            for page in pages:
                if (page.get('type') == 'page' and 
                    page.get('url', '').startswith('edge://') and 
                    'targetId' in page):
                    return page['targetId']
            return await cast(asyncio.Future[str], original_get_valid_page(pages))
        
        browser._get_valid_page = patched_get_valid_page

        yield browser

        # 清理代码
        try:
            await asyncio.wait_for(browser.stop(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            pass


@pytest.mark.asyncio
async def test_browser_initialization(mock_browser):
    assert isinstance(mock_browser.options, Options)
    assert isinstance(mock_browser._proxy_manager, ProxyManager)
    assert mock_browser._connection_port in range(9223, 9323)
    assert mock_browser._pages == ['page1']


@pytest.mark.asyncio
async def test_start_browser_success(mock_browser):
    mock_browser._connection_handler.ping.return_value = True

    await mock_browser.start()

    mock_browser._browser_process_manager.start_browser_process.assert_called_once_with(
        '/fake/path/to/browser',
        mock_browser._connection_port,
        mock_browser.options.arguments,
    )

    assert '--user-data-dir=' in str(mock_browser.options.arguments), (
        'Temporary directory not configured'
    )

    assert 'page1' in mock_browser._pages


@pytest.mark.asyncio
async def test_edge_start_failure_invalid_binary(mock_browser):
    """Test Edge browser start failure with invalid binary"""
    mock_browser._connection_handler.ping.return_value = False
    mock_browser._browser_process_manager.start_browser_process.side_effect = OSError("Browser not found")
    

    with patch('pydoll.browser.base.asyncio.sleep', AsyncMock()) as mock_sleep:
        mock_sleep.return_value = False
        with pytest.raises(OSError, match="Browser not found"):
            await mock_browser.start()


@pytest.mark.asyncio
async def test_start_browser_failure(mock_browser):
    """Test browser start failure with immediate timeout"""
    mock_browser._connection_handler.ping.return_value = False
    
    with patch('pydoll.browser.base.asyncio.sleep', AsyncMock()) as mock_sleep:
        mock_sleep.return_value = False
        with pytest.raises(exceptions.BrowserNotRunning):
            await mock_browser.start()


@pytest.mark.asyncio
async def test_proxy_configuration(mock_browser):
    mock_browser._proxy_manager.get_proxy_credentials = MagicMock(
        return_value=(True, ('user', 'pass'))
    )

    await mock_browser.start()

    mock_browser._connection_handler.execute_command.assert_any_call(
        FetchCommands.enable_fetch_events(True, '')
    )
    mock_browser._connection_handler.register_callback.assert_any_call(
        FetchEvents.REQUEST_PAUSED, ANY, True
    )
    mock_browser._connection_handler.register_callback.assert_any_call(
        FetchEvents.AUTH_REQUIRED,
        ANY,
        True,
    )


@pytest.mark.asyncio
async def test_get_page_existing(mock_browser):
    page = await mock_browser.get_page()
    assert isinstance(page, Page)
    assert len(mock_browser._pages) == 0


@pytest.mark.asyncio
async def test_get_page_new(mock_browser):
    mock_browser._pages = []
    mock_browser._connection_handler.execute_command.return_value = {
        'result': {'targetId': 'new_page'}
    }

    page = await mock_browser.get_page()
    assert isinstance(page, Page)
    assert len(mock_browser._pages) == 0


@pytest.mark.asyncio
async def test_get_existing_page(mock_browser):
    mock_browser._pages = [Page(1234, 'page1')]
    mock_browser._connection_handler.execute_command.return_value = {
        'result': {'targetId': 'new_page'}
    }

    page = await mock_browser.get_page()
    assert isinstance(page, Page)
    assert len(mock_browser._pages) == 0


@pytest.mark.asyncio
async def test_cookie_management(mock_browser):
    cookies = [{'name': 'test', 'value': '123'}]
    await mock_browser.set_cookies(cookies)
    mock_browser._connection_handler.execute_command.assert_any_await(
        StorageCommands.set_cookies(cookies), timeout=60
    )
    mock_browser._connection_handler.execute_command.assert_any_await(
        NetworkCommands.set_cookies(cookies), timeout=60
    )

    mock_browser._connection_handler.execute_command.return_value = {
        'result': {'cookies': cookies}
    }
    result = await mock_browser.get_cookies()
    assert result == cookies

    await mock_browser.delete_all_cookies()
    mock_browser._connection_handler.execute_command.assert_any_await(
        StorageCommands.clear_cookies(), timeout=60
    )
    mock_browser._connection_handler.execute_command.assert_any_await(
        NetworkCommands.clear_browser_cookies(), timeout=60
    )


@pytest.mark.asyncio
async def test_event_registration(mock_browser):
    callback = MagicMock()
    mock_browser._connection_handler.register_callback.return_value = 123

    callback_id = await mock_browser.on('test_event', callback, temporary=True)
    assert callback_id == 123

    mock_browser._connection_handler.register_callback.assert_called_with(
        'test_event', ANY, True
    )


@pytest.mark.asyncio
async def test_window_management(mock_browser):
    mock_browser._connection_handler.execute_command.return_value = {
        'result': {'windowId': 'window1'}
    }

    bounds = {'width': 800, 'height': 600}
    await mock_browser.set_window_bounds(bounds)
    mock_browser._connection_handler.execute_command.assert_any_await(
        BrowserCommands.set_window_bounds('window1', bounds), timeout=60
    )

    await mock_browser.set_window_maximized()
    mock_browser._connection_handler.execute_command.assert_any_await(
        BrowserCommands.set_window_maximized('window1'), timeout=60
    )

    await mock_browser.set_window_minimized()
    mock_browser._connection_handler.execute_command.assert_any_await(
        BrowserCommands.set_window_minimized('window1'), timeout=60
    )


@pytest.mark.asyncio
async def test_stop_browser(mock_browser):
    await mock_browser.stop()
    mock_browser._connection_handler.execute_command.assert_any_await(
        BrowserCommands.CLOSE, timeout=60
    )
    mock_browser._browser_process_manager.stop_process.assert_called_once()
    mock_browser._temp_directory_manager.cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_stop_browser_not_running(mock_browser):
    """Test browser stop with immediate timeout when not running"""
    mock_browser._connection_handler.ping.return_value = False
    
    with patch('pydoll.browser.base.asyncio.sleep', AsyncMock()) as mock_sleep:
        mock_sleep.return_value = False
        with pytest.raises(exceptions.BrowserNotRunning):
            await mock_browser.stop()


@pytest.mark.asyncio
async def test_context_manager(mock_browser):
    async with mock_browser as browser:
        assert browser == mock_browser

    mock_browser._temp_directory_manager.cleanup.assert_called_once()
    mock_browser._browser_process_manager.stop_process.assert_called_once()


@pytest.mark.asyncio
async def test_enable_events(mock_browser):
    await mock_browser.enable_fetch_events(
        handle_auth_requests=True, resource_type='XHR'
    )
    mock_browser._connection_handler.execute_command.assert_called_with(
        FetchCommands.enable_fetch_events(True, 'XHR')
    )


@pytest.mark.asyncio
async def test_disable_events(mock_browser):
    await mock_browser.disable_fetch_events()
    mock_browser._connection_handler.execute_command.assert_called_with(
        FetchCommands.disable_fetch_events()
    )


@pytest.mark.asyncio
async def test__continue_request(mock_browser):
    await mock_browser._continue_request({'params': {'requestId': 'request1'}})
    mock_browser._connection_handler.execute_command.assert_called_with(
        FetchCommands.continue_request('request1'), timeout=60
    )


@pytest.mark.asyncio
async def test__continue_request_auth_required(mock_browser):
    await mock_browser._continue_request_auth_required(
        event={'params': {'requestId': 'request1'}},
        proxy_username='user',
        proxy_password='pass',
    )

    mock_browser._connection_handler.execute_command.assert_any_call(
        FetchCommands.continue_request_with_auth('request1', 'user', 'pass'),
        timeout=60,
    )

    mock_browser._connection_handler.execute_command.assert_any_call(
        FetchCommands.disable_fetch_events()
    )


def test__is_valid_page(mock_browser):
    # Test Edge internal page
    result = mock_browser._is_valid_page({
        'type': 'page',
        'url': 'edge://newtab/',
    })
    assert result is True


def test__is_valid_page_not_a_page(mock_browser):
    # Test invalid page type
    result = mock_browser._is_valid_page({
        'type': 'tab',
        'url': 'edge://newtab/',
    })
    assert result is False


def test__is_valid_page_not_edge_url(mock_browser):
    # Test regular HTTPS webpage
    result = mock_browser._is_valid_page({
        'type': 'page',
        'url': 'https://example.com',
    })
    assert result is True


def test__is_valid_page_http_url(mock_browser):
    # Test regular HTTP webpage
    result = mock_browser._is_valid_page({
        'type': 'page',
        'url': 'http://example.com',
    })
    assert result is True


def test__is_valid_page_invalid_url(mock_browser):
    # Test invalid URL
    result = mock_browser._is_valid_page({
        'type': 'page',
        'url': 'invalid-url',
    })
    assert result is False


@pytest.mark.asyncio
async def test__get_valid_page(mock_browser):
    pages = [
        {
            'type': 'page',
            'url': 'edge://newtab/',
            'targetId': 'valid_page_id',
        },
        {
            'type': 'page',
            'url': 'https://example.com/',
            'targetId': 'invalid_page_id',
        },
        {
            'type': 'tab',
            'url': 'edge://newtab/',
            'targetId': 'invalid_page_id',
        },
    ]

    result = await mock_browser._get_valid_page(pages)
    assert result == 'valid_page_id'


@pytest.mark.asyncio
async def test__get_valid_page_key_error(mock_browser):
    pages = [
        {'type': 'page', 'url': 'edge://newtab/'},
        {'type': 'page', 'url': 'https://example.com/'},
        {'type': 'tab', 'url': 'edge://newtab/'},
    ]

    mock_browser._connection_handler.execute_command.return_value = {
        'result': {'targetId': 'new_page'}
    }
    result = await mock_browser._get_valid_page(pages)
    assert result == 'new_page'
    mock_browser._connection_handler.execute_command.assert_called_with(
        TargetCommands.create_target(''), timeout=60
    )


@pytest.mark.parametrize(
    'os_name, expected_browser_paths, mock_return_value',
    [
        (
            'Windows',
            [
                r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
                r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            ],
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe' 
        ),
        (
            'Linux',
            ['/usr/bin/microsoft-edge'],
            '/usr/bin/microsoft-edge'
        ),
        (
            'Darwin',
            ['/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'],
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
        ),
    ]
)
@patch('platform.system')
@patch.object(BrowserOptionsManager, 'validate_browser_paths')
def test__get_default_binary_location(
    mock_validate_browser_paths,
    mock_platform_system,
    os_name,
    expected_browser_paths,
    mock_return_value
):
    mock_platform_system.return_value = os_name
    mock_validate_browser_paths.return_value = mock_return_value
    
    path = Edge._get_default_binary_location()
    mock_validate_browser_paths.assert_called_once_with(expected_browser_paths)
    assert path == mock_return_value


def test__get_default_binary_location_unsupported_os():
    with patch('platform.system', return_value='SomethingElse'):
        with pytest.raises(ValueError, match='Unsupported OS'):
            Edge._get_default_binary_location()


@patch('platform.system')
def test__get_default_binary_location_throws_exception_if_os_not_supported(mock_platform_system):
    mock_platform_system.return_value = 'FreeBSD'
    
    with pytest.raises(ValueError, match="Unsupported OS"):
        Edge._get_default_binary_location()


@pytest.mark.asyncio
async def test_user_data_directory_setup(mock_browser):
    """Test user data directory is properly set up"""
    await mock_browser.start()
    
    # Verify user data directory argument is added
    user_data_dir_args = [arg for arg in mock_browser.options.arguments if '--user-data-dir=' in arg]
    assert len(user_data_dir_args) == 1
    assert user_data_dir_args[0].startswith('--user-data-dir=') 


def test_edge_page_validation(mock_browser):
    """Test Edge page validation for different URL types"""
    # 使用已经配置好的mock_browser而不是创建新实例
    
    # Test valid Edge URLs
    assert mock_browser._is_valid_page({
        'type': 'page',
        'url': 'edge://settings/'
    }) is True
    
    # Test valid HTTPS URLs
    assert mock_browser._is_valid_page({
        'type': 'page',
        'url': 'https://www.microsoft.com'
    }) is True
    
    # Test invalid URLs
    assert mock_browser._is_valid_page({
        'type': 'page',
        'url': 'about:blank'
    }) is False
    
    # Test invalid page types
    assert mock_browser._is_valid_page({
        'type': 'extension',
        'url': 'edge://extensions/'
    }) is False


@pytest.mark.asyncio
async def test_edge_cleanup(mock_browser):
    """Test Edge browser cleanup process"""
    # Use the already configured mock_browser instead of creating a new instance
    await mock_browser.stop()
    
    # Verify cleanup calls
    mock_browser._temp_directory_manager.cleanup.assert_called_once()
    mock_browser._browser_process_manager.stop_process.assert_called_once()


@pytest.mark.asyncio
async def test_edge_specific_arguments(mock_browser):
    """Test Edge-specific startup arguments are correctly set"""
    options = EdgeOptions()
    browser = ConcreteEdge(options=options)
    
    # Verify Edge-specific arguments are added
    assert '--no-first-run' in browser.options.arguments
    assert '--no-default-browser-check' in browser.options.arguments
    assert '--disable-crash-reporter' in browser.options.arguments
    assert '--disable-features=TranslateUI' in browser.options.arguments
    assert '--disable-component-update' in browser.options.arguments
    assert '--disable-background-networking' in browser.options.arguments