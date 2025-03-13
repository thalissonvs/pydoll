import asyncio
from abc import ABC, abstractmethod
from functools import partial
from random import randint

from pydoll import exceptions
from pydoll.browser.managers import (
    BrowserOptionsManager,
    BrowserProcessManager,
    ProxyManager,
    TempDirectoryManager,
)
from pydoll.browser.options import Options
from pydoll.browser.page import Page
from pydoll.commands.browser import BrowserCommands
from pydoll.commands.dom import DomCommands
from pydoll.commands.fetch import FetchCommands
from pydoll.commands.network import NetworkCommands
from pydoll.commands.page import PageCommands
from pydoll.commands.storage import StorageCommands
from pydoll.commands.target import TargetCommands
from pydoll.connection.connection import ConnectionHandler
from pydoll.events.fetch import FetchEvents


class Browser(ABC):  # noqa: PLR0904
    """
    A class to manage a browser instance for automated interactions.

    This class allows users to start and stop a browser, take screenshots,
    and register event callbacks.
    """

    def __init__(
        self,
        options: Options | None = None,
        connection_port: int = None,
    ):
        """
        Initializes the Browser instance.

        Args:
            options (Options | None): An instance of the Options class to
            configure the browser. If None, default options will be used.
            connection_port (int): The port to connect to the browser.

        Raises:
            TypeError: If any of the arguments are not callable.
        """
        self.options = BrowserOptionsManager.initialize_options(options)
        self._proxy_manager = ProxyManager(self.options)
        self._connection_port = (
            connection_port if connection_port else randint(9223, 9322)
        )
        self._browser_process_manager = BrowserProcessManager()
        self._temp_directory_manager = TempDirectoryManager()
        self._connection_handler = ConnectionHandler(self._connection_port)
        BrowserOptionsManager.add_default_arguments(self.options)

        self._pages = []

    async def __aenter__(self):
        """
        Async context manager entry point.

        Returns:
            Browser: The browser instance.
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Async context manager exit point.

        Args:
            exc_type: The exception type, if raised.
            exc_val: The exception value, if raised.
            exc_tb: The traceback, if an exception was raised.
        """
        await self.stop()
        await self._connection_handler.close()

    async def start(self) -> None:
        """
        Main method to start the browser.

        This method initializes the browser process and configures
        all necessary settings to create a working browser instance.

        Returns:
            None
        """
        binary_location = (
            self.options.binary_location or self._get_default_binary_location()
        )

        self._setup_user_dir()
        proxy_config = self._proxy_manager.get_proxy_credentials()

        self._browser_process_manager.start_browser_process(
            binary_location,
            self._connection_port,
            self.options.arguments,
        )
        await self._verify_browser_running()
        await self._configure_proxy(proxy_config[0], proxy_config[1])
        await self._init_first_page()

    async def set_download_path(self, path: str):
        """
        Sets the download path for the browser.
        Args:
            path (str): The path to the download directory.
        """
        await self._execute_command(BrowserCommands.set_download_path(path))

    async def get_page_by_id(self, page_id: str) -> Page:
        """
        Retrieves a Page instance by its ID.

        Args:
            page_id (str): The ID of the page to retrieve.

        Returns:
            Page: The Page instance corresponding to the specified ID.
        """
        return Page(self._connection_port, page_id)

    async def get_page(self) -> Page:
        """
        Retrieves a Page instance for an existing page in the browser.
        If no pages are open, a new page will be created.

        Returns:
            Page: A Page instance connected to an existing or new browser page.
        """
        page_id = (
            await self.new_page() if not self._pages else self._pages.pop()
        )
        return Page(self._connection_port, page_id)

    async def delete_all_cookies(self):
        """
        Deletes all cookies from the browser.
        """
        await self._execute_command(StorageCommands.clear_cookies())
        await self._execute_command(NetworkCommands.clear_browser_cookies())

    async def set_cookies(self, cookies: list[dict]):
        """
        Sets cookies in the browser.

        Args:
            cookies (list[dict]): A list of dictionaries containing
               the cookie data.
        """
        await self._execute_command(StorageCommands.set_cookies(cookies))
        await self._execute_command(NetworkCommands.set_cookies(cookies))

    async def get_cookies(self):
        """
        Retrieves all cookies from the browser.

        Returns:
            list[dict]: A list of dictionaries containing the cookie data.
        """
        response = await self._execute_command(StorageCommands.get_cookies())
        return response['result']['cookies']

    async def on(
        self, event_name: str, callback: callable, temporary: bool = False
    ) -> int:
        """
        Registers an event callback for a specific event. This method has
        a global scope and can be used to listen for events across all pages
        in the browser. Each `Page` instance also has an `on` method that
        allows for listening to events on a specific page.

        Args:
            event_name (str): Name of the event to listen for.
            callback (callable): Function to be called when the event occurs.
            temporary (bool): If True, the callback will be removed after it's
                triggered once. Defaults to False.

        Returns:
            int: The ID of the registered callback.
        """

        async def callback_wrapper(event):
            asyncio.create_task(callback(event))

        if asyncio.iscoroutinefunction(callback):
            function_to_register = callback_wrapper
        else:
            function_to_register = callback
        return await self._connection_handler.register_callback(
            event_name, function_to_register, temporary
        )

    async def new_page(self, url: str = ''):
        """
        Opens a new page in the browser.

        Args:
            url (str): Optional initial URL to navigate to.
                Defaults to empty string.

        Returns:
            str: The ID of the new page.
        """
        response = await self._execute_command(
            TargetCommands.create_target(url)
        )
        page_id = response['result']['targetId']
        return page_id

    async def get_targets(self):
        """
        Retrieves the list of open pages in the browser.

        Returns:
            list: The list of open pages in the browser.
        """
        response = await self._execute_command(TargetCommands.get_targets())
        return response['result']['targetInfos']

    async def stop(self):
        """
        Stops the running browser process.

        Raises:
            ValueError: If the browser is not currently running.
        """
        if await self._is_browser_running():
            await self._execute_command(BrowserCommands.CLOSE)
            self._browser_process_manager.stop_process()
            await asyncio.sleep(1)  # small delay to ensure browser is closed
            self._temp_directory_manager.cleanup()
        else:
            raise exceptions.BrowserNotRunning('Browser is not running')

    async def get_window_id(self):
        """
        Retrieves the ID of the current browser window.

        Returns:
            str: The ID of the current browser window.
        """
        response = await self._execute_command(BrowserCommands.get_window_id())
        return response['result']['windowId']

    async def set_window_bounds(self, bounds: dict):
        """
        Sets the bounds of the specified window.

        Args:
            bounds (dict): The bounds to set for the window.
        """
        window_id = await self.get_window_id()
        await self._execute_command(
            BrowserCommands.set_window_bounds(window_id, bounds)
        )

    async def set_window_maximized(self):
        """
        Maximizes the specified window.
        """
        window_id = await self.get_window_id()
        await self._execute_command(
            BrowserCommands.set_window_maximized(window_id)
        )

    async def set_window_minimized(self):
        """
        Minimizes the specified window.
        """
        window_id = await self.get_window_id()
        await self._execute_command(
            BrowserCommands.set_window_minimized(window_id)
        )

    async def enable_page_events(self):
        """
        Enables listening for page-related events over the websocket
        connection. Once this method is invoked, the connection will emit
        events pertaining to page activities, such as loading, navigation,
        and DOM updates, to any registered event callbacks. For a comprehensive
        list of available page events and their purposes, refer to the
        PageEvents class documentation.
        This functionality is crucial for monitoring and reacting to changes
        in the page state in real-time.

        This method has a global scope and can be used to listen
        for events across all pages in the browser. Each Page instance also
        has an `enable_page_events` method that allows for listening to events
        on a specific page.

        Returns:
            None
        """
        await self._connection_handler.execute_command(
            PageCommands.enable_page()
        )

    async def enable_network_events(self):
        """
        Activates listening for network events through the websocket
        connection. After calling this method, the connection will emit
        events related to network activities, such as resource loading and
        response status, to any registered event callbacks. This is essential
        for debugging network interactions and analyzing resource requests.
        For details on available network events, consult the NetworkEvents
        class documentation.

        This method has a global scope and can be used to listen
        for events across all pages in the browser. Each Page instance also
        has an `enable_network_events` method that allows for listening to
        events on a specific page.

        Returns:
            None
        """
        await self._connection_handler.execute_command(
            NetworkCommands.enable_network_events()
        )

    async def enable_fetch_events(
        self, handle_auth_requests: bool = False, resource_type: str = ''
    ):
        """
        Enables the Fetch domain for intercepting network requests before they
        are sent. This method allows you to modify, pause, or continue requests
        as needed. If handle_auth_requests is set to True, the connection will
        emit an event when an authentication is required during a request.
        The resource_type parameter specifies which type of requests to
        intercept; if omitted, all requests will be intercepted. Use the
        _continue_request method to resume any paused requests. This is
        especially useful for monitoring and controlling network interactions.

        This method has a global scope and can be used to intercept request
        across all pages in the browser. Each Page instance also has an
        `enable_fetch_events` method that allows for intercepting requests
        on a specific page.

        Args:
            handle_auth_requests (bool): Whether to handle authentication
            requests that require user credentials.
            resource_type (str): The type of resource to intercept (e.g.,
            'XHR', 'Script'). If not specified, all requests will
            be intercepted.

        Returns:
            None
        """
        await self._connection_handler.execute_command(
            FetchCommands.enable_fetch_events(
                handle_auth_requests, resource_type
            )
        )

    async def enable_dom_events(self):
        """
        Enables DOM-related events for the websocket connection. When invoked,
        this method allows the connection to listen for changes in the DOM,
        including node additions, removals, and attribute changes. This feature
        is vital for applications that need to react to dynamic changes in
        the page structure. For a full list of available DOM events, refer to
        the DomCommands class documentation.

        This method has a global scope and can be used to listen
        for events across all pages in the browser. Each Page instance also has
        an `enable_dom_events` method that allows for listening to events on
        a specific page.

        Returns:
            None
        """
        await self._connection_handler.execute_command(
            DomCommands.enable_dom_events()
        )

    async def disable_fetch_events(self):
        """
        Deactivates the Fetch domain, stopping the interception of network
        requests for the websocket connection. Once this method is called,
        the connection will no longer monitor or pause any network requests,
        allowing normal network operations to resume. This can be useful when
        you want to halt the monitoring of network activity.

        This method has a global scope and can be used to disable fetch events
        across all pages in the browser. Each Page instance also has a
        `disable_fetch_events` method that allows for disabling fetch events
        on a specific page.

        Returns:
            None
        """
        await self._connection_handler.execute_command(
            FetchCommands.disable_fetch_events()
        )

    async def _continue_request(self, event: dict):
        """
        Resumes a network request that was previously paused in the browser.
        When the Fetch domain is active, certain requests can be paused based
        on the specified resource type. This method takes the event data that
        contains the request ID and uses it to continue the paused request,
        allowing the browser to proceed with the network operation. This is
        particularly useful for handling requests that require conditional
        logic before they are sent to the server.

        Args:
            event (dict): A dictionary containing the event data, including
            the request ID, which identifies the paused request to be resumed.

        Returns:
            None
        """
        request_id = event['params']['requestId']
        await self._execute_command(FetchCommands.continue_request(request_id))

    async def _continue_request_auth_required(
        self, event: dict, proxy_username: str, proxy_password: str
    ):
        """
        Resumes a network request that was previously paused in the browser
        and requires proxy authentication. This method is triggered when an
        authentication challenge is encountered during the request handling.
        It uses the provided proxy credentials to continue the request,
        enabling successful communication through the proxy server. After
        handling the request, it disables fetch event monitoring.

        Args:
            event (dict): A dictionary containing the event data, which
            includes the request ID for the paused request that needs
            to be resumed.
            proxy_username (str): The username for the proxy server
            authentication.
            proxy_password (str): The password for the proxy server
            authentication.

        Raises:
            IndexError: If the event data does not contain a valid request ID.

        Returns:
            None
        """
        request_id = event['params']['requestId']
        await self._execute_command(
            FetchCommands.continue_request_with_auth(
                request_id, proxy_username, proxy_password
            )
        )
        await self.disable_fetch_events()

    async def _init_first_page(self):
        """
        Initializes the first page in the browser.

        This method obtains the first valid page from available targets
        and stores its ID for later use.

        Returns:
            None
        """
        pages = await self.get_targets()
        valid_page = await self._get_valid_page(pages)
        self._pages.append(valid_page)

    async def _verify_browser_running(self):
        """
        Verifies if the browser is running.

        Raises:
            BrowserNotRunning: If the browser failed to start.
        """
        if not await self._is_browser_running():
            raise exceptions.BrowserNotRunning('Failed to start browser')

    async def _configure_proxy(self, private_proxy, proxy_credentials):
        """
        Configures proxy settings if needed.

        Args:
            private_proxy: Boolean indicating if a private proxy is enabled.
            proxy_credentials: Tuple containing proxy username and password.

        Returns:
            None
        """
        if private_proxy:
            await self.enable_fetch_events(handle_auth_requests=True)
            await self.on(
                FetchEvents.REQUEST_PAUSED,
                self._continue_request,
                temporary=True,
            )
            await self.on(
                FetchEvents.AUTH_REQUIRED,
                partial(
                    self._continue_request_auth_required,
                    proxy_username=proxy_credentials[0],
                    proxy_password=proxy_credentials[1],
                ),
                temporary=True,
            )

    @staticmethod
    def _is_valid_page(page: dict) -> bool:
        """
        Verifies if a page is a valid new tab.

        Args:
            page (dict): Dictionary containing page information.

        Returns:
            bool: True if the page is a valid new tab, False otherwise.
        """
        return page.get('type') == 'page' and 'chrome://newtab/' in page.get(
            'url', ''
        )

    async def _get_valid_page(self, pages) -> str:
        """
        Gets the ID of a valid page or creates a new one.

        Args:
            pages (list): List of page dictionaries to check for validity.

        Returns:
            str: The target ID of an existing or new page.
        """
        valid_page = next(
            (page for page in pages if self._is_valid_page(page)), None
        )

        if valid_page:
            try:
                return valid_page['targetId']
            except KeyError:
                pass

        return await self.new_page()

    async def _is_browser_running(self, timeout: int = 10) -> bool:
        """
        Checks if the browser process is currently running.
        Attempts to connect to the browser to verify its status.

        Returns:
            bool: True if the browser is running, False otherwise.
        """
        for _ in range(timeout):
            if await self._connection_handler.ping():
                return True
            await asyncio.sleep(1)
        return False

    async def _execute_command(self, command: str):
        """
        Executes a command through the connection handler.

        Args:
            command (str): The command to be executed.

        Returns:
            The response from executing the command.
        """
        return await self._connection_handler.execute_command(
            command, timeout=60
        )

    def _setup_user_dir(self):
        """
        Prepares the user data directory if needed.

        This method creates a temporary directory for browser data if
        no user directory is specified in the browser options.

        Returns:
            None
        """
        temp_dir = self._temp_directory_manager.create_temp_dir()
        if '--user-data-dir' not in [
            arg.split('=')[0] for arg in self.options.arguments
        ]:
            self.options.arguments.append(f'--user-data-dir={temp_dir.name}')

    @abstractmethod
    def _get_default_binary_location(self) -> str:
        """
        Retrieves the default location of the browser binary.

        This method must be implemented by subclasses.
        """
        pass
