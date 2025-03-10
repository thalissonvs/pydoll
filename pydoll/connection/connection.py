import asyncio
import json
import logging
from typing import Callable

import websockets

from pydoll import exceptions
from pydoll.connection.managers import CommandManager, EventsHandler
from pydoll.utils import get_browser_ws_address

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ConnectionHandler:
    """
    A class to handle WebSocket connections for browser automation.

    This class manages the connection to the browser and the associated page,
    providing methods to execute commands and register event callbacks.
    """

    def __init__(
        self,
        connection_port: int,
        page_id: str = 'browser',
        ws_address_resolver: Callable[[int], str] = get_browser_ws_address,
        ws_connector: Callable = websockets.connect,
    ):
        """
        Initializes the ConnectionHandler instance.

        Args:
            connection_port (int): The port to connect to the browser.
            page_id (str): The ID of the page to connect to. Use 'browser'
                for browser-level connections. Defaults to 'browser'.
            ws_address_resolver (Callable): Function to resolve WebSocket
                address from port. Defaults to get_browser_ws_address.
            ws_connector (Callable): Function to establish WebSocket
                connections. Defaults to websockets.connect.

        Returns:
            None
        """
        self._connection_port = connection_port
        self._page_id = page_id
        self._ws_address_resolver = ws_address_resolver
        self._ws_connector = ws_connector
        self._ws_connection = None
        self._command_manager = CommandManager()
        self._events_handler = EventsHandler()
        logger.info('ConnectionHandler initialized.')

    @property
    def network_logs(self):
        """
        Gets all network logs captured by the connection.

        This property provides access to network request and response logs
        that have been captured during the browser session.

        Returns:
            list: A list of network log entries.
        """
        return self._events_handler.network_logs

    @property
    def dialog(self):
        """
        Gets information about the current dialog in the page, if any.

        This property provides access to any active dialog (alert, confirm,
        prompt) that might be present in the page.

        Returns:
            dict or None: Dialog information if a dialog is present,
                None otherwise.
        """
        return self._events_handler.dialog

    async def ping(self) -> bool:
        """
        Sends a ping message to the browser.

        Returns:
            bool: True if the ping was successful, False otherwise.
        """
        try:
            await self._ensure_active_connection()
            await self._ws_connection.ping()
            return True
        except Exception:
            return False

    async def execute_command(self, command: dict, timeout: int = 10) -> dict:
        """
        Sends a command to the browser and awaits its response.

        Args:
            command (dict): The command to send, structured as a dictionary.
            timeout (int, optional): Time in seconds to wait for a response.
                Defaults to 10.

        Returns:
            dict: The response from the browser.

        Raises:
            InvalidCommand: If the command is not a dictionary.
            TimeoutError: If the command execution exceeds the timeout.
        """
        if not isinstance(command, dict):
            logger.error('Command must be a dictionary.')
            raise exceptions.InvalidCommand('Command must be a dictionary')

        await self._ensure_active_connection()
        future = self._command_manager.create_command_future(command)
        command_str = json.dumps(command)

        try:
            await self._ws_connection.send(command_str)
            response: str = await asyncio.wait_for(future, timeout)
            return json.loads(response)
        except asyncio.TimeoutError as exc:
            self._command_manager.remove_pending_command(command['id'])
            raise exc
        except websockets.ConnectionClosed as exc:
            await self._handle_connection_loss()
            raise exc

    async def register_callback(
        self, event_name: str, callback: Callable, temporary: bool = False
    ):
        """
        Registers a callback function for a specific event.

        Args:
            event_name (str): The name of the event to listen for.
            callback (Callable): The function to call when the event occurs.
            temporary (bool): If True, the callback will be removed after it's
                triggered once. Defaults to False.

        Returns:
            int: The ID of the registered callback, which can be used to
                remove the listener later.
        """
        return self._events_handler.register_callback(
            event_name, callback, temporary
        )

    async def remove_callback(self, callback_id: int):
        """
        Removes a registered event callback by its ID.

        Args:
            callback_id (int): The ID of the callback to remove.

        Returns:
            bool: True if the callback was successfully removed,
                False otherwise.
        """
        return self._events_handler.remove_callback(callback_id)

    async def clear_callbacks(self):
        """
        Removes all registered event callbacks.

        This method clears all event listeners that have been registered with
        the register_callback method.

        Returns:
            None
        """
        return self._events_handler.clear_callbacks()

    async def close(self):
        """
        Closes the WebSocket connection.

        Closes the WebSocket connection and clears all event callbacks.
        """
        await self.clear_callbacks()
        await self._ws_connection.close()
        logger.info('WebSocket connection closed.')

    async def _ensure_active_connection(self):
        """
        Guarantees that an active connection exists before proceeding.

        This method checks if the WebSocket connection is established
        and active. If not, it establishes a new connection.

        Returns:
            None
        """
        if self._ws_connection is None or self._ws_connection.closed:
            await self._establish_new_connection()

    async def _establish_new_connection(self):
        """
        Creates a fresh WebSocket connection and starts listening for events.

        This method resolves the appropriate WebSocket address, establishes
        a new connection, and initiates an asynchronous task to receive events.

        Returns:
            None
        """
        ws_address = await self._resolve_ws_address()
        logger.info(f'Connecting to {ws_address}')
        self._ws_connection = await self._ws_connector(ws_address)
        self._receive_task = asyncio.create_task(self._receive_events())
        logger.debug('WebSocket connection established')

    async def _resolve_ws_address(self):
        """
        Determines the correct WebSocket address based on the page ID.

        This method resolves the WebSocket URL differently depending on whether
        the connection is to the browser itself or a specific page.

        Returns:
            str: The WebSocket URL to connect to.
        """
        if 'browser' in self._page_id:
            return await self._ws_address_resolver(self._connection_port)
        return (
            f'ws://localhost:{self._connection_port}/devtools/page/'
            f'{self._page_id}'
        )

    async def _handle_connection_loss(self):
        """
        Cleans up resources after a WebSocket connection loss.

        This method closes the connection if it's still open, nullifies the
        connection reference, and cancels any ongoing receive tasks.

        Returns:
            None
        """
        if self._ws_connection and not self._ws_connection.closed:
            await self._ws_connection.close()
        self._ws_connection = None

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()

        logger.info('Connection resources cleaned up')

    async def _receive_events(self):
        """
        Main loop for receiving and processing incoming WebSocket messages.
        Delegates processing to specialized handlers based on message type.
        """
        try:
            async for raw_message in self._incoming_messages():
                await self._process_single_message(raw_message)
        except websockets.ConnectionClosed as e:
            logger.info(f'Connection closed gracefully: {e}')
        except Exception as e:
            logger.error(f'Unexpected error in event loop: {e}')
            raise

    async def _incoming_messages(self):
        """
        Generator that yields raw messages from the WebSocket connection.

        This asynchronous generator continuously receives messages from the
        WebSocket connection as long as it remains open.

        Yields:
            str: The raw message string received from the WebSocket.
        """
        while not self._ws_connection.closed:
            yield await self._ws_connection.recv()

    async def _process_single_message(self, raw_message: str):
        """
        Orchestrates the processing of a single raw WebSocket message.

        This method parses the raw message string into a JSON object and
        routes it to the appropriate handler based on whether it's a command
        response or an event notification.

        Args:
            raw_message (str): The raw message string to process.

        Returns:
            None
        """
        message = self._parse_message(raw_message)
        if not message:
            return

        if self._is_command_response(message):
            await self._handle_command_message(message)
        else:
            await self._handle_event_message(message)

    @staticmethod
    def _parse_message(raw_message: str) -> dict | None:
        """
        Attempts to parse raw message string into JSON.
        Returns parsed dict or None if parsing fails.

        Args:
            raw_message (str): The raw message string to parse.

        Returns:
            dict | None: The parsed JSON object if successful, None otherwise.
        """
        try:
            return json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning(f'Failed to parse message: {raw_message[:200]}...')
            return None

    @staticmethod
    def _is_command_response(message: dict) -> bool:
        """
        Determines if a message is a response to a previously sent command.

        Command responses are identified by having an integer 'id' field,
        which corresponds to the ID of the original command.

        Args:
            message (dict): The message to check.

        Returns:
            bool: True if the message is a command response, False otherwise.
        """
        return 'id' in message and isinstance(message['id'], int)

    async def _handle_command_message(self, message: dict):
        """
        Processes messages that are responses to previously sent commands.

        This method resolves the future associated with the command ID,
        allowing the calling code to continue execution with the response.

        Args:
            message (dict): The command response message to process.

        Returns:
            None
        """
        logger.debug(f'Processing command response: {message.get("id")}')
        self._command_manager.resolve_command(
            message['id'], json.dumps(message)
        )

    async def _handle_event_message(self, message: dict):
        """
        Processes messages that are spontaneous event notifications.

        This method delegates event processing to the events handler,
        which will invoke any registered callbacks for the event type.

        Args:
            message (dict): The event message to process.

        Returns:
            None
        """
        event_type = message.get('method', 'unknown-event')
        logger.debug(f'Processing {event_type} event')
        await self._events_handler.process_event(message)

    def __repr__(self):
        """
        Returns a string representation of the ConnectionHandler for debugging.

        Returns:
            str: A string representation of the ConnectionHandler.
        """
        return f'ConnectionHandler(port={self._connection_port})'

    def __str__(self):
        """
        Returns a user-friendly string representation of the ConnectionHandler.

        Returns:
            str: A string representation of the ConnectionHandler.
        """
        return f'ConnectionHandler(port={self._connection_port})'

    async def __aenter__(self):
        """
        Async context manager entry point.

        Returns:
            ConnectionHandler: The ConnectionHandler instance.
        """
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Async context manager exit point.

        This method ensures the connection is properly closed when
        exiting the context manager.

        Args:
            exc_type: The exception type, if raised.
            exc_val: The exception value, if raised.
            exc_tb: The traceback, if an exception was raised.

        Returns:
            None
        """
        await self.close()
