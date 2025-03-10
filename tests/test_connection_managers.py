import pytest

from pydoll import exceptions
from pydoll.connection.connection import CommandManager, EventsHandler


@pytest.fixture
def command_manager():
    """Returns a fresh instance of CommandManager for the tests."""
    return CommandManager()


@pytest.fixture
def events_handler():
    """Returns a fresh instance of EventsHandler for the tests."""
    return EventsHandler()


def test_create_command_future(command_manager):
    test_command = {'method': 'TestMethod'}
    future_result = command_manager.create_command_future(test_command)

    # Checks if the ID was assigned correctly.
    assert test_command['id'] == 1, 'The first command ID should be 1'
    # Checks if the future was stored in the pending dictionary.
    assert 1 in command_manager._pending_commands
    assert command_manager._pending_commands[1] is future_result

    # Creates a second command and checks the ID increment.
    second_command = {'method': 'SecondMethod'}
    future_second = command_manager.create_command_future(second_command)
    assert second_command['id'] == 2, 'The second command ID should be 2'
    assert 2 in command_manager._pending_commands
    assert command_manager._pending_commands[2] is future_second


def test_resolve_command(command_manager):
    test_command = {'method': 'TestMethod'}
    future_result = command_manager.create_command_future(test_command)
    result_payload = '{"result": "success"}'

    # The future should not be completed before resolution.
    assert not future_result.done(), (
        'The future should not be completed before resolution'
    )

    # Resolves the command and checks the result.
    command_manager.resolve_command(1, result_payload)
    assert future_result.done(), (
        'The future should be completed after resolution'
    )
    assert future_result.result() == result_payload, (
        'The future result does not match the expected result'
    )
    # The pending command should be removed.
    assert 1 not in command_manager._pending_commands


def test_resolve_unknown_command(command_manager):
    test_command = {'method': 'TestMethod'}
    future_result = command_manager.create_command_future(test_command)

    # Attempts to resolve a nonexistent ID; the original future should remain pending.
    command_manager.resolve_command(999, '{"result": "ignored"}')
    assert not future_result.done(), (
        'The future should not be completed after resolving an unknown command'
    )


def test_remove_pending_command(command_manager):
    test_command = {'method': 'TestMethod'}
    _ = command_manager.create_command_future(test_command)

    # Removes the pending command and checks if it was successfully removed.
    command_manager.remove_pending_command(1)
    assert 1 not in command_manager._pending_commands, (
        'The pending command should be removed'
    )
    command_manager.remove_pending_command(1)


def test_register_callback_success(events_handler):
    dummy_callback = lambda event: event
    callback_id = events_handler.register_callback('TestEvent', dummy_callback)

    assert callback_id == 1, 'The first callback ID should be 1'
    assert callback_id in events_handler._event_callbacks, (
        'The callback must be registered'
    )
    callback_info = events_handler._event_callbacks[callback_id]
    assert callback_info['temporary'] is False, (
        'The temporary flag should be False by default'
    )


def test_register_callback_invalid(events_handler):
    with pytest.raises(exceptions.InvalidCallback):
        events_handler.register_callback('TestEvent', 'Not a callback')


def test_remove_existing_callback(events_handler):
    dummy_callback = lambda event: event
    callback_id = events_handler.register_callback('TestEvent', dummy_callback)
    removal_result = events_handler.remove_callback(callback_id)

    assert removal_result is True, (
        'The removal of a existing callback should be successful'
    )
    assert callback_id not in events_handler._event_callbacks, (
        'The callback should be removed'
    )


def test_remove_nonexistent_callback(events_handler):
    removal_result = events_handler.remove_callback(999)
    assert removal_result is False, (
        'The removal of a nonexistent callback should return False'
    )


def test_clear_callbacks(events_handler):
    dummy_callback = lambda event: event
    events_handler.register_callback('EventA', dummy_callback)
    events_handler.register_callback('EventB', dummy_callback)

    events_handler.clear_callbacks()
    assert len(events_handler._event_callbacks) == 0, (
        'All callbacks should be cleared'
    )


@pytest.mark.asyncio
async def test_process_event_updates_network_logs(events_handler):
    assert events_handler.network_logs == []
    network_event = {
        'method': 'Network.requestWillBeSent',
        'url': 'http://example.com',
    }

    await events_handler.process_event(network_event)

    assert network_event in events_handler.network_logs, (
        'The network event should be added to the logs'
    )


@pytest.mark.asyncio
async def test_process_event_triggers_callbacks(events_handler):
    callback_results = []

    def sync_callback(event):
        callback_results.append(('sync', event.get('value')))

    async def async_callback(event):
        callback_results.append(('async', event.get('value')))

    sync_callback_id = events_handler.register_callback(
        'MyCustomEvent', sync_callback, temporary=True
    )
    async_callback_id = events_handler.register_callback(
        'MyCustomEvent', async_callback, temporary=False
    )

    test_event = {'method': 'MyCustomEvent', 'value': 123}
    await events_handler.process_event(test_event)

    assert ('sync', 123) in callback_results, (
        'The synchronous callback was not triggered correctly'
    )
    assert ('async', 123) in callback_results, (
        'The asynchronous callback was not triggered correctly'
    )

    assert sync_callback_id not in events_handler._event_callbacks, (
        'The temporary callback should be removed after execution'
    )

    assert async_callback_id in events_handler._event_callbacks, (
        'The permanent callback should remain registered'
    )


@pytest.mark.asyncio
async def test_trigger_callbacks_error_handling(events_handler, caplog):
    def faulty_callback(event):
        raise ValueError('Error in callback')

    faulty_callback_id = events_handler.register_callback(
        'ErrorEvent', faulty_callback, temporary=True
    )
    test_event = {'method': 'ErrorEvent'}

    await events_handler.process_event(test_event)
    assert faulty_callback_id not in events_handler._event_callbacks, (
        'The callback with error should be removed after execution'
    )
    error_logged = any(
        'Error in callback' in record.message for record in caplog.records
    )
    assert error_logged, 'The error in the callback should be logged'
