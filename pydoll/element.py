import asyncio
import json

import aiofiles
from bs4 import BeautifulSoup

from pydoll import exceptions
from pydoll.commands.dom import DomCommands
from pydoll.commands.input import InputCommands
from pydoll.commands.page import PageCommands
from pydoll.commands.runtime import RuntimeCommands
from pydoll.connection.connection import ConnectionHandler
from pydoll.constants import Scripts
from pydoll.mixins.find_elements import FindElementsMixin
from pydoll.utils import decode_image_to_bytes


class WebElement(FindElementsMixin):
    """
    Represents a DOM element in the browser.

    This class provides methods to interact with and retrieve information about
    DOM elements. It allows operations such as clicking, sending keys, getting
    attributes, and other common web element interactions. It inherits element
    finding capabilities from FindElementsMixin.
    """
    def __init__(
        self,
        object_id: str,
        connection_handler: ConnectionHandler,
        method: str = None,
        selector: str = None,
        attributes_list: list = [],
    ):
        """
        Initializes the WebElement instance.

        Args:
            object_id (str): The unique object ID for this DOM element.
            connection_handler (ConnectionHandler): The connection instance to
                communicate with the browser.
            method (str, optional): The search method used to find this
                element. Defaults to None.
            selector (str, optional): The selector string used to find this
            element. Defaults to None.
            attributes_list (list, optional): List of attribute name-value
                pairs. Defaults to an empty list.
        """
        self._object_id = object_id
        self._search_method = method
        self._selector = selector
        self._connection_handler = connection_handler
        self._attributes = {}
        self._def_attributes(attributes_list)

    def __repr__(self):
        """
        Returns a string representation of the WebElement.

        The representation includes all attributes and the object ID,
        making it useful for debugging and logging.

        Returns:
            str: String representation of the WebElement.
        """
        attrs = ', '.join(f'{k}={v!r}' for k, v in self._attributes.items())
        return (
            f'{self.__class__.__name__}({attrs})(object_id={self._object_id})'
        )

    def _def_attributes(self, attributes_list: list):
        """
        Defines element attributes from a flat list of key-value pairs.

        This method processes an attribute list from the browser and
        populates the element's attributes dictionary. It handles the
        special case of renaming 'class' to 'class_name' to avoid conflicts
        with Python's reserved keywords.

        Args:
            attributes_list (list): A flat list of alternating attribute names
                and values.

        Returns:
            None
        """
        for i in range(0, len(attributes_list), 2):
            key = attributes_list[i]
            key = key if key != 'class' else 'class_name'
            value = attributes_list[i + 1]
            self._attributes[key] = value

    @property
    def value(self) -> str:
        """
        Retrieves the value of the element.

        Returns:
            str: The value of the element.
        """
        return self._attributes.get('value')

    @property
    def class_name(self) -> str:
        """
        Retrieves the class name of the element.

        Returns:
            str: The class name of the element.
        """
        return self._attributes.get('class_name')

    @property
    def id(self) -> str:
        """
        Retrieves the id of the element.

        Returns:
            str: The id of the element.
        """
        return self._attributes.get('id')

    @property
    def is_enabled(self) -> bool:
        """
        Retrieves the enabled status of the element.

        Returns:
            bool: The enabled status of the element.
        """
        return bool('disabled' not in self._attributes.keys())

    @property
    async def bounds(self) -> list:
        """
        Asynchronously retrieves the bounding box coordinates of the element.

        This property uses the DevTools Protocol to get detailed positioning
        information of the element.

        Returns:
            list: A list of points defining the element's bounding box.
        """
        command = DomCommands.box_model(object_id=self._object_id)
        response = await self._execute_command(command)
        return response['result']['model']['content']

    @property
    async def inner_html(self) -> str:
        """
        Retrieves the inner HTML of the element.

        Returns:
            str: The inner HTML of the element.
        """
        command = DomCommands.get_outer_html(self._object_id)
        response = await self._execute_command(command)
        return response['result']['outerHTML']

    async def get_bounds_using_js(self) -> list:
        """
        Retrieves the bounding box of the element using JavaScript.

        Returns:
            list: The bounding box of the element.
        """
        response = await self._execute_script(
            Scripts.BOUNDS, return_by_value=True
        )
        return json.loads(response['result']['result']['value'])

    async def _execute_script(
        self, script: str, return_by_value: bool = False
    ):
        """
        Executes a JavaScript script in the context of this element.

        This method allows executing JavaScript with the element as 'this',
        enabling direct manipulation of the element using JavaScript.

        Args:
            script (str): The JavaScript script to execute.
            return_by_value (bool): Whether to return the result by value.
                If False, returns a reference. Defaults to False.

        Returns:
            dict: The response from the browser containing the script execution
                results.
        """
        return await self._execute_command(
            RuntimeCommands.call_function_on(
                self._object_id, script, return_by_value
            )
        )

    async def _is_element_visible(self):
        """
        Verifies if the element is visible using JavaScript.
        It uses the getBoundingClientRect method to check if the element is
        within the viewport and the width and height are greater than 0.

        Returns:
            bool: True if the element is visible, False otherwise.
        """
        result = await self._execute_script(
            Scripts.ELEMENT_VISIBLE, return_by_value=True
        )
        return result['result']['result']['value']

    async def _is_element_on_top(self):
        """
        Verifies if the element is on top of the page using JavaScript.
        It uses the elementFromPoint method to check if the element is the
        topmost element at the center of its bounding box.

        Returns:
            bool: True if the element is on top of the page, False otherwise.
        """
        result = await self._execute_script(
            Scripts.ELEMENT_ON_TOP, return_by_value=True
        )
        return result['result']['result']['value']

    async def get_screenshot(self, path: str):
        """
        Takes a screenshot of the element.

        Args:
            path (str): The path where the screenshot will be saved.
        """
        bounds = await self.get_bounds_using_js()
        clip = {
            'x': bounds['x'],
            'y': bounds['y'],
            'width': bounds['width'],
            'height': bounds['height'],
            'scale': 1,
        }
        screenshot = await self._connection_handler.execute_command(
            PageCommands.screenshot(fmt='jpeg', clip=clip)
        )
        async with aiofiles.open(path, 'wb') as file:
            image_bytes = decode_image_to_bytes(screenshot['result']['data'])
            await file.write(image_bytes)

    async def get_element_text(self) -> str:
        """
        Retrieves the text of the element.

        Returns:
            str: The text of the element.
        """
        outer_html = await self.inner_html
        soup = BeautifulSoup(outer_html, 'html.parser')
        text_inside = soup.get_text(strip=True)
        return text_inside

    def get_attribute(self, name: str) -> str:
        """
        Retrieves the attribute value of the element.

        Args:
            name (str): The name of the attribute.

        Returns:
            str: The value of the attribute.
        """
        return self._attributes.get(name)

    async def scroll_into_view(self):
        """
        Scrolls the element into the visible area of the browser window.

        This method ensures that the element is visible in the viewport
        before performing actions like clicking or getting screenshots.

        Returns:
            None
        """
        command = DomCommands.scroll_into_view(object_id=self._object_id)
        await self._execute_command(command)

    async def click_using_js(self):
        """
        Clicks on the element using JavaScript.

        This method uses JavaScript to trigger a click event on the element.
        It's useful for elements that can't be clicked using normal mouse
        events, or for elements that might be obscured.

        Raises:
            ElementNotVisible: If the element is not visible on the page.
            ElementNotInteractable: If the element could not be clicked.

        Returns:
            None
        """
        if self._is_option_tag():
            return await self.click_option_tag()

        await self.scroll_into_view()

        if not await self._is_element_visible():
            raise exceptions.ElementNotVisible(
                'Element is not visible on the page.'
            )

        result = await self._execute_script(
            Scripts.CLICK, return_by_value=True
        )
        clicked = result['result']['result']['value']
        if not clicked:
            raise exceptions.ElementNotInteractable(
                'Element is not interactable.'
            )

    async def click(self, x_offset: int = 0, y_offset: int = 0):
        """
        Clicks on the element using mouse events.

        This method simulates a real mouse click by sending mouse press and
        release events at the center of the element. It can also click at
        an offset from the center if specified.

        Args:
            x_offset (int): Horizontal offset from the center of the element.
                Defaults to 0.
            y_offset (int): Vertical offset from the center of the element.
                Defaults to 0.

        Raises:
            ElementNotVisible: If the element is not visible on the page.

        Returns:
            None
        """
        if self._is_option_tag():
            return await self.click_option_tag()

        if not await self._is_element_visible():
            raise exceptions.ElementNotVisible(
                'Element is not visible on the page.'
            )

        await self.scroll_into_view()

        try:
            element_bounds = await self.bounds
            position_to_click = self._calculate_center(element_bounds)
            position_to_click = (
                position_to_click[0] + x_offset,
                position_to_click[1] + y_offset,
            )
        except KeyError:
            element_bounds = await self.get_bounds_using_js()
            position_to_click = (
                element_bounds['x'] + element_bounds['width'] / 2,
                element_bounds['y'] + element_bounds['height'] / 2,
            )

        press_command = InputCommands.mouse_press(*position_to_click)
        release_command = InputCommands.mouse_release(*position_to_click)
        await self._connection_handler.execute_command(press_command)
        await asyncio.sleep(0.1)
        await self._connection_handler.execute_command(release_command)

    async def click_option_tag(self):
        """
        Clicks on an option element in a select dropdown.

        This method uses a specialized JavaScript approach to select an option
        in a dropdown, as option elements require different handling than
        standard clickable elements.

        Returns:
            None
        """
        script = Scripts.CLICK_OPTION_TAG.replace('{self.value}', self.value)
        await self._execute_command(RuntimeCommands.evaluate_script(script))

    async def send_keys(self, text: str):
        """
        Sends a sequence of keys to the element.

        Args:
            text (str): The text to send to the element.
        """
        await self._execute_command(InputCommands.insert_text(text))

    async def type_keys(self, text: str):
        """
        Types in a realistic manner by sending keys one by one.

        Args:
            text (str): The text to send to the element.
        """
        for char in text:
            await self._execute_command(InputCommands.key_press(char))
            await asyncio.sleep(0.1)

    def _is_option_tag(self):
        """
        Checks if the element is an option tag in a select dropdown.

        Returns:
            bool: True if the element is an option tag, False otherwise.
        """
        return self._attributes['tag_name'].lower() == 'option'

    @staticmethod
    def _calculate_center(bounds: list) -> tuple:
        """
        Calculates the center point of an element from its bounding box.

        This method processes the coordinates from a bounding box list and
        computes the center point, which is useful for clicking operations.

        Args:
            bounds (list): A list of coordinates defining the element's
                bounding box. Expected format is
                a flat list of [x1, y1, ..., xn, yn].

        Returns:
            tuple: A tuple containing the (x, y) coordinates of
                the center point.
        """
        x_values = [bounds[i] for i in range(0, len(bounds), 2)]
        y_values = [bounds[i] for i in range(1, len(bounds), 2)]
        x_center = sum(x_values) / len(x_values)
        y_center = sum(y_values) / len(y_values)
        return x_center, y_center
