import asyncio
import os
import subprocess
from abc import ABC, abstractmethod
from functools import partial
from random import randint
from tempfile import TemporaryDirectory

from pydoll import exceptions
from pydoll.browser.options import Options
from pydoll.browser.page import Page
from pydoll.commands.browser import BrowserCommands
from pydoll.commands.dom import DomCommands
from pydoll.commands.fetch import FetchCommands
from pydoll.commands.network import NetworkCommands
from pydoll.commands.page import PageCommands
from pydoll.commands.storage import StorageCommands
from pydoll.commands.target import TargetCommands
from pydoll.connection import ConnectionHandler
from pydoll.events.fetch import FetchEvents


class Browser(ABC):
    """
    A class to manage a browser instance for automated interactions.

    This class allows users to start and stop a browser, take screenshots,
    and register event callbacks.
    """

    def __init__(
        self, options: Options | None = None, connection_port: int = None
    ):
        """
        Initializes the Browser instance.

        Args:
            options (Options | None): An instance of the Options class to
            configure the browser. If None, default options will be used.
        """
        self._connection_port = (
            connection_port if connection_port else randint(9223, 9322)
        )
        self.connection_handler = ConnectionHandler(self._connection_port)
        self.options = self._initialize_options(options)
        self.process = None
        self.temp_dirs = []
        self._pages = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        for temp_dir in self.temp_dirs:
            temp_dir.cleanup()
        self.connection_handler.close()

    async def start(self) -> None:
        """
        Starts the browser process with the specified options, including proxy configurations.

        This method initializes and launches the browser, setting up the necessary command-line
        arguments. It checks for a specified user data directory, creating a temporary directory if none
        is provided, and configures the browser to run in a controlled environment.

        Returns:
            Page: The Page instance for the browser.
        """

        binary_location = (
            self.options.binary_location or self._get_default_binary_location()
        )

        self.options.arguments.append('--no-first-run')
        self.options.arguments.append('--no-default-browser-check')
        
        temp_dir = self._get_temp_dir()

        if '--user-data-dir' not in [arg.split('=')[0] for arg in self.options.arguments]:
            self.options.arguments.append(f'--user-data-dir={temp_dir.name}')
        
        private_proxy, proxy_credentials = self._configure_proxy()

        self.process = subprocess.Popen(
            [
                binary_location,
                f'--remote-debugging-port={self._connection_port}',
                *self.options.arguments,
            ],
            stdout=subprocess.PIPE,
        )
        if not await self._is_browser_running():
            raise exceptions.BrowserNotRunning('Failed to start browser')

        if private_proxy:
            await self.enable_fetch_events(handle_auth_requests=True)
            await self.on(
                FetchEvents.REQUEST_PAUSED,
                self._continue_request,
                temporary=True,
            )
            # partial is used to send extra arguments to the callback
            # and keep the callback as a coroutine function
            await self.on(
                FetchEvents.AUTH_REQUIRED,
                partial(
                    self._continue_request_auth_required,
                    proxy_username=proxy_credentials[0],
                    proxy_password=proxy_credentials[1],
                ),
                temporary=True,
            )

        pages = await self.get_pages()
        valid_page = [
            page
            for page in pages
            if page['type'] == 'page' and 'chrome' not in page['title']
        ][0]['targetId']
        self._pages.append(valid_page)

    async def get_page(self) -> Page:
        """
        Retrieves a Page instance for an existing page in the browser.
        If no pages are open, a new page will be created.
        """
        if not self._pages:
            await self.new_page()

        page_id = self._pages.pop()
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
            cookies (list[dict]): A list of dictionaries containing the cookie data.
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
    ):
        """
        Registers an event callback for a specific event. This method has a global
        scope and can be used to listen for events across all pages in the browser.
        Each `Page` instance also has an `on` method that allows for listening to
        events on a specific page.

        Args:
            event_name (str): Name of the event to listen for.
            callback (Callable): function to be called when the event occurs.
        """

        async def callback_wrapper(event):
            asyncio.create_task(callback(event))

        if asyncio.iscoroutinefunction(callback):
            function_to_register = callback_wrapper
        else:
            function_to_register = callback

        await self.connection_handler.register_callback(
            event_name, function_to_register, temporary
        )

    async def new_page(self, url: str = ''):
        """
        Opens a new page in the browser.

        Returns:
            Page: The new page instance.
        """
        response = await self._execute_command(
            TargetCommands.create_target(url)
        )
        page_id = response['result']['targetId']
        self._pages.append(page_id)

    async def get_pages(self):
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
            self.process.terminate()
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

    async def _is_browser_running(self):
        """
        Checks if the browser process is currently running.
        Attempts to connect to the browser to verify its status.

        Returns:
            bool: True if the browser is running, False otherwise.
        """
        for _ in range(10):
            if await self._check_browser_connection():
                return True
            await asyncio.sleep(1)
        return False

    async def _check_browser_connection(self):
        """
        Checks if the browser process is currently running.

        Returns:
            bool: True if the browser is running, False otherwise.
        """
        try:
            await self.connection_handler.connection
            return True
        except Exception as exc:
            print(f'Browser is not running: {exc}')
            return False

    async def _execute_command(self, command: str):
        """
        Executes a command through the connection handler.

        Args:
            command (str): The command to be executed.

        Returns:
            The response from executing the command.
        """
        return await self.connection_handler.execute_command(
            command, timeout=60
        )

    def _configure_proxy(self) -> tuple[bool, tuple[str, str]]:
        """
        Configures the proxy settings for the browser. If the proxy
        is private, the credentials will be extracted from the proxy
        string and returned.

        Returns:
            tuple[bool, tuple[str, str]]: A tuple containing a boolean
            indicating if the proxy is private and a tuple with the proxy
            username and password
        """
        private_proxy = False
        proxy_username, proxy_password = None, None

        if any('--proxy-server' in arg for arg in self.options.arguments):
            proxy_index = next(
                index
                for index, arg in enumerate(self.options.arguments)
                if '--proxy-server' in arg
            )
            proxy = self.options.arguments[proxy_index].replace(
                '--proxy-server=', ''
            )

            if '@' in proxy:
                credentials, proxy_server = proxy.split('@')
                self.options.arguments[proxy_index] = (
                    f'--proxy-server={proxy_server}'
                )
                proxy_username, proxy_password = credentials.split(':')
                private_proxy = True

        return private_proxy, (proxy_username, proxy_password)

    async def enable_page_events(self):
        """
        Enables listening for page-related events over the websocket connection.
        Once this method is invoked, the connection will emit events pertaining
        to page activities, such as loading, navigation, and DOM updates, to any
        registered event callbacks. For a comprehensive list of available page
        events and their purposes, refer to the PageEvents class documentation.
        This functionality is crucial for monitoring and reacting to changes
        in the page state in real-time.

        This method has a global scope and can be used to listen for events across
        all pages in the browser. Each Page instance also has an `enable_page_events`
        method that allows for listening to events on a specific page.

        Returns:
            None
        """
        await self.connection_handler.execute_command(
            PageCommands.enable_page()
        )

    async def enable_network_events(self):
        """
        Activates listening for network events through the websocket connection.
        After calling this method, the connection will emit events related
        to network activities, such as resource loading and response status,
        to any registered event callbacks. This is essential for debugging
        network interactions and analyzing resource requests. For details
        on available network events, consult the NetworkEvents class documentation.

        This method has a global scope and can be used to listen for events across
        all pages in the browser. Each Page instance also has an `enable_network_events`
        method that allows for listening to events on a specific page.

        Returns:
            None
        """
        await self.connection_handler.execute_command(
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
        The resource_type parameter specifies which type of requests to intercept;
        if omitted, all requests will be intercepted. Use the _continue_request
        method to resume any paused requests. This is especially useful for
        monitoring and controlling network interactions.

        This method has a global scope and can be used to intercept requests across
        all pages in the browser. Each Page instance also has an `enable_fetch_events`
        method that allows for intercepting requests on a specific page.

        Args:
            handle_auth_requests (bool): Whether to handle authentication
            requests that require user credentials.
            resource_type (str): The type of resource to intercept (e.g.,
            'XHR', 'Script'). If not specified, all requests will be intercepted.

        Returns:
            None
        """
        await self.connection_handler.execute_command(
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

        This method has a global scope and can be used to listen for events across
        all pages in the browser. Each Page instance also has an `enable_dom_events`
        method that allows for listening to events on a specific page.

        Returns:
            None
        """
        await self.connection_handler.execute_command(
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
        await self.connection_handler.execute_command(
            FetchCommands.disable_fetch_events()
        )

    async def _continue_request(self, event: dict):
        """
        Resumes a network request that was previously paused in the browser.
        When the Fetch domain is active, certain requests can be paused based
        on the specified resource type. This method takes the event data that
        contains the request ID and uses it to continue the paused request,
        allowing the browser to proceed with the network operation. This is
        particularly useful for handling requests that require conditional logic
        before they are sent to the server.

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
        It uses the provided proxy credentials to continue the request, enabling
        successful communication through the proxy server. After handling the
        request, it disables fetch event monitoring.

        Args:
            event (dict): A dictionary containing the event data, which includes
            the request ID for the paused request that needs to be resumed.
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

    @staticmethod
    def _validate_browser_path(path: str):
        """
        Validates the provided browser path.

        Args:
            path (str): The file path to the browser executable.

        Raises:
            ValueError: If the browser path does not exist.

        Returns:
            str: The validated browser path.
        """
        if not os.path.exists(path):
            raise ValueError(f'Browser not found: {path}')
        return path

    @staticmethod
    def _initialize_options(options: Options | None) -> Options:
        """
        Initializes the options for the browser.

        Args:
            options (Options | None): An instance of the Options class or None.

        Raises:
            ValueError: If the provided options are invalid.

        Returns:
            Options: The initialized options instance.
        """
        if options is None:
            return Options()
        if not isinstance(options, Options):
            raise ValueError('Invalid options')
        return options

    def _get_temp_dir(self):
        """
        Retrieves a temporary directory for the browser instance.

        Returns:
            TemporaryDirectory: The temporary directory.
        """
        temp_dir = TemporaryDirectory()
        self.temp_dirs.append(temp_dir)
        return temp_dir

    @abstractmethod
    def _get_default_binary_location(self) -> str:
        """
        Retrieves the default location of the browser binary.

        This method must be implemented by subclasses.
        """
        pass
