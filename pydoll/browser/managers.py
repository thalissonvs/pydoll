import os
import shutil
import subprocess
from contextlib import suppress
from tempfile import TemporaryDirectory

from pydoll.browser.options import ChromeOptions, EdgeOptions, Options
from pydoll.browser.constants import BrowserType


class ProxyManager:
    def __init__(self, options):
        """
        Initializes the ProxyManager with browser options.

        This manager handles proxy configuration for the browser,
        including extraction and management of proxy credentials.

        Args:
            options: The browser options instance containing arguments.
        """
        self.options = options

    def get_proxy_credentials(self) -> tuple[bool, tuple[str, str]]:
        """
        Configures proxy settings and extracts credentials if present.

        This method searches for proxy settings in the browser options,
        extracts any credentials, and updates the proxy arguments to use
        a clean proxy URL without embedded credentials.

        Returns:
            tuple[bool, tuple[str, str]]: A tuple containing:
                - bool: True if private proxy with credentials was found
                - tuple[str, str]: Username and password for proxy
                    authentication
        """
        private_proxy = False
        credentials = (None, None)

        proxy_arg = self._find_proxy_argument()

        if proxy_arg is not None:
            index, proxy_value = proxy_arg
            has_credentials, username, password, clean_proxy = (
                self._parse_proxy(proxy_value)
            )

            if has_credentials:
                self._update_proxy_argument(index, clean_proxy)
                private_proxy = True
                credentials = (username, password)

        return private_proxy, credentials

    def _find_proxy_argument(self) -> tuple[int, str] | None:
        """
        Finds the first valid --proxy-server argument in browser options.

        This method iterates through the browser arguments looking for
        a proxy server configuration.

        Returns:
            tuple[int, str] | None: A tuple containing the index of the
                argument and the proxy value if found, None otherwise.
        """
        for index, arg in enumerate(self.options.arguments):
            if arg.startswith('--proxy-server='):
                return index, arg.split('=', 1)[1]
        return None

    @staticmethod
    def _parse_proxy(proxy_value: str) -> tuple[bool, str, str, str]:
        """
        Extracts credentials from proxy value and cleans the proxy string.

        This method parses a proxy URL to extract embedded credentials
        (if present) in the format username:password@server.

        Args:
            proxy_value (str): The proxy URL potentially containing
                credentials.

        Returns:
            tuple[bool, str, str, str]: A tuple containing:
                - bool: True if credentials were found
                - str: Username (or None if no credentials)
                - str: Password (or None if no credentials)
                - str: Clean proxy URL without credentials
        """
        if '@' not in proxy_value:
            return False, None, None, proxy_value

        try:
            creds_part, server_part = proxy_value.split('@', 1)
            username, password = creds_part.split(':', 1)
            return True, username, password, server_part
        except ValueError:
            return False, None, None, proxy_value

    def _update_proxy_argument(self, index: int, clean_proxy: str) -> None:
        """
        Updates the options arguments list with the clean proxy URL.

        This method replaces the original proxy argument (which may have
        contained credentials) with a clean version that doesn't expose
        sensitive data.

        Args:
            index (int): The index of the proxy argument to update.
            clean_proxy (str): The proxy URL without credentials.

        Returns:
            None
        """
        self.options.arguments[index] = f'--proxy-server={clean_proxy}'


class BrowserProcessManager:
    def __init__(self, process_creator=None):
        """
        Initializes the BrowserProcessManager.

        This manager handles the creation and management of browser processes.

        Args:
            process_creator (callable, optional): A function that creates a
                browser process. If None, the default process creator is used.
        """
        self._process_creator = (
            process_creator or self._default_process_creator
        )
        self._process = None

    def start_browser_process(
        self, binary_location: str, port: int, arguments: list
    ) -> None:
        """
        Starts the browser process with the given parameters.

        This method launches a new browser process with the specified binary,
        debugging port, and command-line arguments.

        Args:
            binary_location (str): Path to the browser executable.
            port (int): The remote debugging port to use.
            arguments (list): Additional command-line arguments for the
                browser.

        Returns:
            subprocess.Popen: The started browser process.
        """
        self._process = self._process_creator([
            binary_location,
            f'--remote-debugging-port={port}',
            *arguments,
        ])
        return self._process

    @staticmethod
    def _default_process_creator(command: list[str]):
        """
        Default function to create a browser process.

        This method creates a subprocess with the given command-line arguments.

        Args:
            command (list[str]): The command and arguments to start the
                process.

        Returns:
            subprocess.Popen: The created process instance.
        """
        return subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

    def stop_process(self):
        """
        Stops the browser process if it's running.

        This method terminates the browser process that was previously
        started with start_browser_process.

        Returns:
            None
        """
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()


class TempDirectoryManager:
    def __init__(self, temp_dir_factory=TemporaryDirectory):
        """
        Initializes the TempDirectoryManager.

        This manager handles the creation and cleanup of temporary directories
        used by browser instances.

        Args:
            temp_dir_factory (callable, optional): A function that creates
                temporary directories. Defaults to TemporaryDirectory.
        """
        self._temp_dir_factory = temp_dir_factory
        self._temp_dirs = []

    def create_temp_dir(self):
        """
        Creates a temporary directory for a browser instance.

        This method creates a new temporary directory and tracks it
        for later cleanup.

        Returns:
            TemporaryDirectory: The created temporary directory instance.
        """
        temp_dir = self._temp_dir_factory()
        self._temp_dirs.append(temp_dir)
        return temp_dir

    def cleanup(self):
        """
        Cleans up all temporary directories created by this manager.

        This method removes all temporary directories created with
        create_temp_dir, suppressing any OS errors that might occur
        during deletion.

        Returns:
            None
        """
        for temp_dir in self._temp_dirs:
            with suppress(OSError):
                shutil.rmtree(temp_dir.name)


class BrowserOptionsManager:
    @staticmethod
    def initialize_options(options: Options | None, browser_type: BrowserType = None) -> Options:
        """
        Initialize browser options based on browser type.

        Creates a new options instance based on browser type if none is provided,
        or validates and returns the provided options instance.

        Args:
            options (Options | None): Browser options instance. If None, a new instance
                will be created based on browser_type
            browser_type (BrowserType): Type of browser, used to create appropriate
                options instance

        Returns:
            Options: The initialized browser options instance

        Raises:
            ValueError: If provided options is not an instance of Options class
        """
        if options is None:
            if browser_type == BrowserType.CHROME:
                return ChromeOptions()
            elif browser_type == BrowserType.EDGE:
                return EdgeOptions()
            else:
                return Options()
        
        if not isinstance(options, Options):
            raise ValueError('Invalid options')
            
        return options

    @staticmethod
    def add_default_arguments(options: Options):
        """Adds default arguments to the provided options"""
        options.arguments.append('--no-first-run')
        options.arguments.append('--no-default-browser-check')
        
        # Add browser-specific arguments based on options type
        if isinstance(options, EdgeOptions):
            BrowserOptionsManager._add_edge_arguments(options)
        elif isinstance(options, ChromeOptions):
            BrowserOptionsManager._add_chrome_arguments(options)

    @staticmethod
    def _add_edge_arguments(options: Options):
        """Adds Edge-specific arguments to the options"""
        options.add_argument('--disable-crash-reporter')
        options.add_argument('--disable-features=TranslateUI')
        options.add_argument('--disable-component-update')
        options.add_argument('--disable-background-networking')
        options.add_argument('--remote-allow-origins=*')

    @staticmethod
    def _add_chrome_arguments(options: Options):
        """Adds Chrome-specific arguments to the options"""
        options.add_argument('--remote-allow-origins=*')
        # Add other Chrome-specific arguments here

    @staticmethod
    def validate_browser_paths(paths: list[str]) -> str:
        """
        Validates the provided browser executable path.

        This method checks if the browser executable file exists at
        the specified path.

        Args:
            paths (list[str]): Lista de caminhos possíveis do navegador.


        Returns:
            str: The validated browser path if it exists.

        Raises:
            ValueError: If the browser executable is not found at the path.
        """
        for path in paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                return path
        raise ValueError(f"No valid browser path found in: {paths}")
