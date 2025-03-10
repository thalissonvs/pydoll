import asyncio

from pydoll import exceptions
from pydoll.commands.dom import DomCommands
from pydoll.commands.runtime import RuntimeCommands


def create_web_element(*args, **kwargs):
    """
    Creates a WebElement instance while avoiding circular imports.

    This function is used as a factory to create WebElement instances
    by dynamically importing the WebElement class. This approach
    prevents circular import issues that would occur with direct imports.

    Args:
        *args: Positional arguments to pass to the WebElement constructor.
        **kwargs: Keyword arguments to pass to the WebElement constructor.

    Returns:
        WebElement: A new WebElement instance.
    """
    from pydoll.element import WebElement  # noqa: PLC0415

    return WebElement(*args, **kwargs)


class FindElementsMixin:
    """
    A mixin class that provides element finding and waiting capabilities.

    This mixin provides methods for finding elements in the DOM using various
    selector strategies, waiting for elements to appear, and interacting with
    elements. Classes that include this mixin will gain the ability to locate
    elements in web pages.
    """
    async def wait_element(
        self,
        by: DomCommands.SelectorType,
        value: str,
        timeout: int = 10,
        raise_exc: bool = True,
    ):
        """
        Waits for an element to be present in the DOM.

        This method repeatedly attempts to find an element until it is found or
        the timeout is reached. It is useful for handling dynamic content that
        may not be immediately available.

        Args:
            by (SelectorType): The type of selector to use
                (e.g., 'css', 'xpath').
            value (str): The value of the selector to locate the element.
            timeout (int): Maximum time in seconds to wait for the element.
                Defaults to 10 seconds.
            raise_exc (bool): Whether to raise an exception if the element
                is not found within the timeout. Defaults to True.

        Returns:
            WebElement or None: The element found in the DOM, or None if
                not found and raise_exc is False.

        Raises:
            TimeoutError: If the element is not found within the timeout and
                raise_exc is True.
        """
        start_time = asyncio.get_event_loop().time()
        while True:
            try:
                element = await self.find_element(by, value, raise_exc=False)
                if element:
                    return element
            except exceptions.ElementNotFound:
                pass

            if asyncio.get_event_loop().time() - start_time > timeout:
                if raise_exc:
                    raise TimeoutError('Element not found')
                return None

            await asyncio.sleep(0.5)

    async def find_element(
        self, by: DomCommands.SelectorType, value: str, raise_exc: bool = True
    ):
        """
        Finds an element on the current page using the specified selector.

        This method locates the first element matching the given selector and
        returns a WebElement instance representing that element. If no element
        is found, it either raises an exception or returns None, depending on
        the raise_exc parameter.

        Args:
            by (SelectorType): The type of selector to use
                (e.g., 'css', 'xpath').
            value (str): The value of the selector to locate the element.
            raise_exc (bool): Whether to raise an exception if the element
                is not found. Defaults to True.

        Returns:
            WebElement or None: The found element as a WebElement instance, or
                None if no element is found and raise_exc is False.

        Raises:
            ElementNotFound: If the element is not found and raise_exc is True.
        """
        if hasattr(self, '_object_id'):
            command = DomCommands.find_element(by, value, self._object_id)
        else:
            command = DomCommands.find_element(by, value)

        response = await self._execute_command(command)

        if not response.get('result', {}).get('result', {}).get('objectId'):
            if raise_exc:
                raise exceptions.ElementNotFound('Element not found')
            return None

        object_id = response['result']['result']['objectId']
        node_description = await self._describe_node(object_id=object_id)
        attributes = node_description.get('attributes', [])

        tag_name = node_description.get('nodeName', '').lower()
        attributes.extend(['tag_name', tag_name])

        return create_web_element(
            object_id, self._connection_handler, by, value, attributes
        )

    async def find_elements(
        self, by: DomCommands.SelectorType, value: str, raise_exc: bool = True
    ):
        """
        Finds all elements on the current page using the specified selector.

        This method locates all elements matching the given selector and
        returns a list of WebElement instances. If no elements are found,
        it either raises an exception or returns an empty list, depending on
        the raise_exc parameter.

        Args:
            by (SelectorType): The type of selector to use
                (e.g., 'css', 'xpath').
            value (str): The value of the selector to locate the elements.
            raise_exc (bool): Whether to raise an exception if no elements are
                found. Defaults to True.

        Returns:
            list[WebElement]: A list of WebElement instances representing the
                found elements. Returns an empty list if no elements are found
                and raise_exc is False.

        Raises:
            ElementNotFound: If no elements are found and raise_exc is True.
        """
        if hasattr(self, '_object_id'):
            command = DomCommands.find_elements(by, value, self._object_id)
        else:
            command = DomCommands.find_elements(by, value)

        response = await self._execute_command(command)

        if not response.get('result', {}).get('result', {}).get('objectId'):
            if raise_exc:
                raise exceptions.ElementNotFound('Element not found')
            return []

        object_id = response['result']['result']['objectId']
        query_response = await self._execute_command(
            RuntimeCommands.get_properties(object_id=object_id)
        )
        response = []
        for query in query_response['result']['result']:
            query_value = query.get('value', {})
            if query_value and query_value['type'] == 'object':
                response.append(query_value['objectId'])

        elements = []
        for object_id in response:
            try:
                node_description = await self._describe_node(
                    object_id=object_id
                )
            except KeyError:
                continue

            attributes = node_description.get('attributes', [])
            tag_name = node_description.get('nodeName', '').lower()
            attributes.extend(['tag_name', tag_name])

            elements.append(
                create_web_element(
                    object_id, self._connection_handler, by, value, attributes
                )
            )
        return elements

    async def _describe_node(self, object_id: str = '') -> dict:
        """
        Provides a detailed description of a specific node within the DOM.

        This method retrieves detailed information about a DOM node using its
        object ID. The information includes the node's attributes, properties,
        and relationship to other nodes.

        Args:
            object_id (str): The unique object ID of the node to describe.
                Defaults to an empty string, which typically refers to the
                document node.

        Returns:
            dict: A dictionary containing the detailed description of the node,
                including its attributes, properties, and other
                characteristics.
        """
        response = await self._execute_command(
            DomCommands.describe_node(object_id=object_id)
        )
        return response['result']['node']

    async def _execute_command(self, command: dict) -> dict:
        """
        Executes a DevTools Protocol command on the page.

        This is an internal method used to send commands to the browser and
        receive responses. It uses the connection handler to communicate with
        the browser and has a longer timeout to accommodate potentially
        time-consuming DOM operations.

        Args:
            command (dict): The DevTools Protocol command to execute.

        Returns:
            dict: The result of the command execution as returned by
                the browser.
        """
        return await self._connection_handler.execute_command(
            command, timeout=60
        )
