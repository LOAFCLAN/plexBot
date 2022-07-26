
from discord.abc import Messageable
from discord.iterators import HistoryIterator
from discord_components.dpy_overrides import *


# Override the channel.history() method include message components

class CustomHistoryIterator(HistoryIterator):

    def __init__(self, messageable, limit,
                 before=None, after=None, around=None, oldest_first=None):
        super().__init__(messageable, limit, before, after, around, oldest_first)

    async def fill_messages(self):
        if not hasattr(self, 'channel'):
            # do the required set up
            channel = await self.messageable._get_channel()
            self.channel = channel

        if self._get_retrieve():
            data = await self._retrieve_messages(self.retrieve)
            if len(data) < 100:
                self.limit = 0  # terminate the infinite loop

            if self.reverse:
                data = reversed(data)
            if self._filter:
                data = filter(self._filter, data)

            channel = self.channel
            for element in data:
                await self.messages.put(ComponentMessage(state=self.state, channel=channel, data=element))


# Returns a modified async iterator
def history(channel, *args, **kwargs) -> HistoryIterator:
    return CustomHistoryIterator(channel, *args, **kwargs)


Messageable.history = history
