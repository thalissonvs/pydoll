from unittest.mock import MagicMock, Mock, patch

import pytest

from pydoll.browser.managers import (
    BrowserOptionsManager,
    BrowserProcessManager,
    ProxyManager,
    TempDirectoryManager,
)
from pydoll.browser.options import Options


@pytest.fixture
def proxy_options():
    return Options()


@pytest.fixture
def temp_manager():
    mock_dir = MagicMock()
    mock_dir.name = '/fake/temp/dir'
    mock_dir.cleanup = MagicMock()
    return TempDirectoryManager(
        temp_dir_factory=lambda delete: mock_dir
    )


@pytest.fixture
def process_manager():
    mock_creator = Mock(return_value=MagicMock())
    return BrowserProcessManager(process_creator=mock_creator)


def test_proxy_manager_no_proxy(proxy_options):
    manager = ProxyManager(proxy_options)
    result = manager.get_proxy_credentials()

    assert result[0] is False
    assert result[1] == (None, None)


def test_proxy_manager_with_credentials(proxy_options):
    proxy_options.add_argument('--proxy-server=user:pass@example.com')
    manager = ProxyManager(proxy_options)
    result = manager.get_proxy_credentials()

    assert result[0] is True
    assert result[1] == ('user', 'pass')
    assert proxy_options.arguments == ['--proxy-server=example.com']


def test_proxy_manager_invalid_credentials_format(proxy_options):
    proxy_options.add_argument('--proxy-server=invalidformat@example.com')
    manager = ProxyManager(proxy_options)
    result = manager.get_proxy_credentials()

    assert result[0] is False
    assert result[1] == (None, None)
    assert proxy_options.arguments == [
        '--proxy-server=invalidformat@example.com'
    ]


def test_proxy_manager_invalid_proxy_format(proxy_options):
    proxy_options.add_argument('--proxy-server=invalidformat')
    manager = ProxyManager(proxy_options)
    result = manager.get_proxy_credentials()

    assert result[0] is False
    assert result[1] == (None, None)


def test_start_browser_process(process_manager):
    binary = '/fake/path/browser'
    port = 9222
    args = ['--test-arg']

    process_manager.start_browser_process(binary, port, args)

    expected_command = [binary, f'--remote-debugging-port={port}', *args]
    process_manager._process_creator.assert_called_once_with(expected_command)
    assert process_manager._process is not None


def test_stop_process(process_manager):
    mock_process = MagicMock()
    process_manager._process = mock_process

    process_manager.stop_process()

    mock_process.terminate.assert_called_once()


def test_create_temp_dir(temp_manager):
    temp_dir = temp_manager.create_temp_dir()

    assert len(temp_manager._temp_dirs) == 1
    assert temp_dir.name == '/fake/temp/dir'


def test_cleanup_temp_dirs(temp_manager):
    mock_dir1 = MagicMock()
    mock_dir2 = MagicMock()
    temp_manager._temp_dirs = [mock_dir1, mock_dir2]

    temp_manager.cleanup()

    assert mock_dir1.cleanup.call_count == 1
    assert mock_dir2.cleanup.call_count == 1


def test_initialize_options_with_none():
    result = BrowserOptionsManager.initialize_options(None)

    assert isinstance(result, Options)
    assert result.arguments == []


def test_initialize_options_with_valid_options():
    options = Options()
    options.add_argument('--test')
    result = BrowserOptionsManager.initialize_options(options)

    assert result is options
    assert result.arguments == ['--test']


def test_initialize_options_with_invalid_type():
    with pytest.raises(ValueError):
        BrowserOptionsManager.initialize_options('invalid')


def test_add_default_arguments():
    options = Options()
    BrowserOptionsManager.add_default_arguments(options)

    assert '--no-first-run' in options.arguments
    assert '--no-default-browser-check' in options.arguments


def test_validate_browser_path_valid():
    with patch('os.path.exists', return_value=True):
        result = BrowserOptionsManager.validate_browser_path('/fake/path')
        assert result == '/fake/path'


def test_validate_browser_path_invalid():
    with patch('os.path.exists', return_value=False):
        with pytest.raises(ValueError):
            BrowserOptionsManager.validate_browser_path('/fake/path')
