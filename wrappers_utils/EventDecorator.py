import asyncio
import functools

from loguru import logger as logging


class EventManager:
    def __init__(self):
        self.event_handlers = {}
        self.instances = []

    def add_event_handler(self, event_name, handler):
        if event_name not in self.event_handlers:
            self.event_handlers[event_name] = []
        self.event_handlers[event_name].append(handler)

    def add_instance(self, cls):
        self.instances.append(cls)

    def trigger_event(self, event_name, *args, **kwargs):
        if event_name in self.event_handlers:
            for handler in self.event_handlers[event_name]:
                # Find the instance that the handler belongs to
                for instance in self.instances:
                    if handler.__qualname__.startswith(instance.__class__.__qualname__):
                        # Run the handler in a separate thread
                        # print(f"Running {handler.__qualname__} with"
                        #       f" args self={instance}, *args={args}, **kwargs={kwargs}")
                        asyncio.create_task(handler(instance, *args, **kwargs))
                        break


event_manager = EventManager()


def on_event(event_name):
    def decorator(func):
        # Validate the function is a coroutine
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("The function must be a coroutine")

        event_manager.add_event_handler(event_name, func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return decorator
