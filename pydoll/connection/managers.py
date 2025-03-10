import asyncio
import logging
from typing import Callable, Dict

from pydoll import exceptions

logger = logging.getLogger(__name__)


class CommandManager:
    """
    Manages the lifecycle of commands sent to the browser.

    This class handles the creation of command futures, command ID generation,
    and resolution of command responses. It maintains a mapping of command IDs
    to their corresponding futures, allowing asynchronous command execution.
    """

    def __init__(self):
        """
        Initializes the CommandManager.

        Sets up internal state for tracking pending commands and
        initializes the command ID counter.

        Returns:
            None
        """
        self._pending_commands: dict[int, asyncio.Future] = {}
        self._id = 1

    def create_command_future(self, command: dict) -> asyncio.Future:
        """
        Creates a future for a command and assigns it a unique ID.

        This method assigns a unique ID to the command, creates a future
        to track its completion, and stores the future in the pending
        commands dictionary.

        Args:
            command (dict): The command to prepare for execution.

        Returns:
            asyncio.Future: A future that will be resolved when the command
                completes.
        """
        command['id'] = self._id
        future = asyncio.Future()
        self._pending_commands[self._id] = future
        self._id += 1
        return future

    def resolve_command(self, response_id: int, result: str):
        """
        Resolves a pending command with its result.

        This method sets the result for the future associated with the
        command ID and removes it from the pending commands dictionary.

        Args:
            response_id (int): The ID of the command to resolve.
            result (str): The result data for the command.

        Returns:
            None
        """
        if response_id in self._pending_commands:
            self._pending_commands[response_id].set_result(result)
            del self._pending_commands[response_id]

    def remove_pending_command(self, command_id: int):
        """
        Removes a pending command without resolving it.

        This method is useful for handling timeouts or cancellations,
        allowing cleanup of command futures that will never be resolved.

        Args:
            command_id (int): The ID of the command to remove.

        Returns:
            None
        """
        if command_id in self._pending_commands:
            del self._pending_commands[command_id]


class EventsHandler:
    """
    Manages event callbacks, event processing, and network logs.

    This class is responsible for registering event callbacks, triggering them
    when events are received, and maintaining state related to events such as
    network logs and dialog information.
    """

    def __init__(self):
        """
        Initializes the EventsHandler.

        Sets up internal state for tracking event callbacks, initializes
        the callback ID counter, and creates empty collections for network
        logs and dialog information.

        Returns:
            None
        """
        self._event_callbacks: Dict[int, dict] = {}
        self._callback_id = 0
        self.network_logs = []
        self.dialog = {}
        logger.info('EventsHandler initialized')

    def register_callback(
        self, event_name: str, callback: Callable, temporary: bool = False
    ) -> int:
        """
        Registers a callback for a specific event type.

        This method associates a callback function with an event name,
        allowing the function to be called whenever that event occurs.

        Args:
            event_name (str): The name of the event to listen for.
            callback (Callable): The function to call when the event occurs.
            temporary (bool): If True, the callback will be removed after it's
                triggered once. Defaults to False.

        Returns:
            int: The ID of the registered callback, which can be used to
                remove it later.

        Raises:
            InvalidCallback: If the callback is not callable.
        """
        if not callable(callback):
            logger.error('Callback must be a callable function.')
            raise exceptions.InvalidCallback('Callback must be callable')

        self._callback_id += 1
        self._event_callbacks[self._callback_id] = {
            'event': event_name,
            'callback': callback,
            'temporary': temporary,
        }
        logger.info(
            f"Registered callback '{event_name}' with ID {self._callback_id}"
        )
        return self._callback_id

    def remove_callback(self, callback_id: int) -> bool:
        """
        Removes a callback by its ID.

        This method removes a previously registered callback from the
        event handler, preventing it from being triggered in the future.

        Args:
            callback_id (int): The ID of the callback to remove.

        Returns:
            bool: True if the callback was successfully removed, False if
                the callback ID was not found.
        """
        if callback_id not in self._event_callbacks:
            logger.warning(f'Callback ID {callback_id} not found')
            return False

        del self._event_callbacks[callback_id]
        logger.info(f'Removed callback ID {callback_id}')
        return True

    def clear_callbacks(self):
        """
        Removes all registered callbacks.

        This method clears all event listeners that have been registered,
        effectively resetting the event handler to its initial state.

        Returns:
            None
        """
        self._event_callbacks.clear()
        logger.info('All callbacks cleared')

    async def process_event(self, event_data: dict):
        """
        Processes a received event and triggers corresponding callbacks.

        This method handles special events like network requests and dialogs,
        updating internal state accordingly, and then triggers any callbacks
        registered for the event type.

        Args:
            event_data (dict): The event data in dictionary format.

        Returns:
            None
        """
        event_name = event_data.get('method')
        logger.debug(f'Processing event: {event_name}')

        if 'Network.requestWillBeSent' in event_name:
            self._update_network_logs(event_data)

        if 'Page.javascriptDialogOpening' in event_name:
            self.dialog = event_data

        if 'Page.javascriptDialogClosed' in event_name:
            self.dialog = {}

        await self._trigger_callbacks(event_name, event_data)

    def _update_network_logs(self, event_data: dict):
        """
        Maintains the network logs collection.

        This method adds a new network event to the logs and ensures
        the collection doesn't grow too large by limiting its size.

        Args:
            event_data (dict): The network event data to add to the logs.

        Returns:
            None
        """
        self.network_logs.append(event_data)
        self.network_logs = self.network_logs[-10000:]  # Mantém tamanho máximo

    async def _trigger_callbacks(self, event_name: str, event_data: dict):
        """
        Triggers all registered callbacks for an event.

        This method iterates through all registered callbacks for the
        specified event name and invokes them with the event data.
        It also handles temporary callbacks by removing them after they're
        triggered.

        Args:
            event_name (str): The name of the event that occurred.
            event_data (dict): The data associated with the event.

        Returns:
            None
        """
        callbacks_to_remove = []

        for cb_id, cb_data in list(self._event_callbacks.items()):
            if cb_data['event'] == event_name:
                try:
                    if asyncio.iscoroutinefunction(cb_data['callback']):
                        await cb_data['callback'](event_data)
                    else:
                        cb_data['callback'](event_data)
                except Exception as e:
                    logger.error(f'Error in callback {cb_id}: {str(e)}')

                if cb_data['temporary']:
                    callbacks_to_remove.append(cb_id)

        for cb_id in callbacks_to_remove:
            self.remove_callback(cb_id)
