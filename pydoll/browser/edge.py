import os
import platform
from typing import Optional

from pydoll.browser.base import Browser
from pydoll.browser.managers import BrowserOptionsManager
from pydoll.browser.options import Options
from pydoll.browser.constants import BrowserType
class Edge(Browser):
    def __init__(
        self,
        options: Optional[Options] = None,
        connection_port: Optional[int] = None,
    ):
        super().__init__(options, connection_port, BrowserType.EDGE)
        

    @staticmethod
    def _get_default_binary_location():
        """
        Get the default Edge browser binary location based on the operating system.
        
        Returns:
            str: Path to the Edge browser executable
            
        Raises:
            ValueError: If the operating system is not supported
        """
        os_name = platform.system()
        browser_paths = {
            'Windows':
                r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            'Linux':
                '/usr/bin/microsoft-edge',
            'Darwin':
                '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
        }

        browser_path = browser_paths.get(os_name)

        if not browser_path:
            raise ValueError('Unsupported operating system')

        return BrowserOptionsManager.validate_browser_path(
            browser_path
        )