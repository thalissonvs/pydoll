import platform
from typing import Optional

from pydoll.browser.base import Browser
from pydoll.browser.managers import BrowserOptionsManager
from pydoll.browser.options import Options
from pydoll.browser.constants import BrowserType

class Chrome(Browser):
    """
    A class that implements the Chrome browser functionality.

    This class provides specific implementation for launching and
    controlling Google Chrome browsers.
    """

    def __init__(
        self,
        options: Optional[Options] = None,
        connection_port: Optional[int] = None,
    ):
        """
        Initializes the Chrome browser instance.

        Args:
            options (Options | None): An instance of Options class to configure
                the browser. If None, default options will be used.
            connection_port (int): The port to connect to the browser.
                Defaults to 9222.
        """
        super().__init__(options, connection_port, BrowserType.CHROME)

    @staticmethod
    def _get_default_binary_location():
        """
        Gets the default location of the Chrome browser executable.

        This method determines the default Chrome executable path based
        on the operating system.

        Returns:
            str: The path to the Chrome browser executable.

        Raises:
            ValueError: If the operating system is not supported or
                the browser executable is not found at the default location.
        """
        os_name = platform.system()

        browser_paths = {
            'Windows': [
                r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            ],
            'Linux': [
                '/usr/bin/google-chrome',
            ],
            'Darwin': [
                '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            ]
        }

        browser_path = browser_paths.get(os_name)

        if not browser_path:
            raise ValueError('Unsupported OS')

        return BrowserOptionsManager.validate_browser_paths(
            browser_path
        )
