import asyncio

import discord
import plexapi
from discord.ext.commands import command, has_permissions, Cog, BadArgument
from loguru import logger as logging

from utils import get_from_media_index, safe_field, base_info_layer


class PlexEvents(Cog):
    class PlexMediaEvent:

        def __init__(self, itemID, message):
            self.message = message
            self.itemID = itemID

            self.title = "This media has no title and this should never be matched"

        def __eq__(self, other):
            return other['itemID'] == self.itemID or other['title'] == self.title

    def __init__(self, bot):
        self.bot = bot
        table = self.bot.database.get_table("plex_alert_channel")
        self.plex_alert_channels = table.get_all()

        self.event_tracker = {}
        self.listener_tasks = {}

    @Cog.listener()
    async def on_ready(self):
        logging.info("PlexEvents is ready")
        for entry in self.plex_alert_channels:
            self.bot.loop.create_task(self.start_event_listener(entry[0], entry[1]))

    async def start_event_listener(self, guild_id, channel_id):
        """Starts the event listener"""

        channel = self.bot.get_channel(channel_id)
        guild = self.bot.get_guild(guild_id)
        plex = await self.bot.fetch_plex(guild)

        event_queue = asyncio.Queue()

        def event_callback(data):
            if data['type'] == 'timeline':
                entry = data['TimelineEntry'][0]
                if entry['identifier'] == 'com.plexapp.plugins.library':
                    event_queue.put_nowait(entry)

        listener = plexapi.alert.AlertListener(plex, event_callback, self.event_error)
        listener.start()
        task = self.bot.loop.create_task(self.event_message_loop(plex, event_queue, channel))
        self.listener_tasks[guild_id] = task
        logging.info(f"Started event listener for {guild.name}")
        while listener.is_alive():
            await asyncio.sleep(1)
        logging.warning(f"Event listener for {guild.name} has stopped")

    def event_error(self, error):
        print(error)

    async def event_message_loop(self, plex, queue, channel):
        self.event_tracker[channel.guild.id] = []
        lock = asyncio.Lock()
        while True:
            try:
                event = await queue.get()
                if event is None:
                    break
                if int(event['sectionID']) == -1:
                    continue
                print(event)
                await lock.acquire()  # Ensure only one event is processed at a time
                await self.send_event_message(plex, channel, event)
                lock.release()
                await asyncio.sleep(1)
            except Exception as e:
                logging.error(e)
                logging.exception(e)

    def get_media_event(self, guild_id, itemID, title=None):
        for event in self.event_tracker[guild_id]:
            if event.itemID == itemID or event.title == title:
                return event
        return None

    async def send_event_message(self, plex, channel, event):
        embed = discord.Embed()

        # only include messages with an ID of 0, 5, 9
        if event['state'] == 0:
            embed.title = "New Media Added"
            embed.color = 0x00ff00
            library = plex.library.sectionByID(int(event['sectionID']))
            embed.description = f"New media file added to {library.title}"
            embed.set_footer(text=f"Waiting for item matching to complete")
            msg = await channel.send(embed=embed)
            self.event_tracker[channel.guild.id].append(self.PlexMediaEvent(event['itemID'], msg))
        elif event['state'] == 1:  # Matching item
            if event in self.event_tracker[channel.guild.id]:
                event_obj = self.get_media_event(channel.guild.id, event['itemID'])
                event_obj.title = event['title']
                embed.title = "New Media Added"
                embed.color = 0x00ff00
                library = plex.library.sectionByID(int(event['sectionID']))
                embed.description = f"Media `{event['title']}` added to `{library.title}`"
                embed.set_footer(text=f"Waiting for metadata download to start, media ID: {event['itemID']}")
                await event_obj.message.edit(embed=embed)
        elif event['state'] == 3:  # metadata download started
            if event in self.event_tracker[channel.guild.id]:
                event_obj = self.get_media_event(channel.guild.id, event['itemID'], event['title'])
                event_obj.itemID = event['itemID']
                embed.title = "New Media Added"
                embed.color = 0x00ff00
                library = plex.library.sectionByID(int(event['sectionID']))
                embed.description = f"Media `{event['title']}` added to `{library.title}`"
                embed.set_footer(text=f"Waiting for metadata download to finish, media ID: {event['itemID']}")
                msg = self.get_media_event(channel.guild.id, event['itemID']).message
                await msg.edit(embed=embed)
        elif event['state'] == 5:
            if event in self.event_tracker[channel.guild.id]:
                library = plex.library.sectionByID(int(event['sectionID']))
                media = get_from_media_index(library, event['itemID'])
                if media is not None:
                    if media.isPartialObject():  # For some reason plex likes to not give everything we asked for
                        media.reload()
                    if isinstance(media, plexapi.video.Movie):
                        embed.title = safe_field(f"{media.title} ({media.year})")
                    elif isinstance(media, plexapi.video.Episode):
                        embed.title = f"{media.grandparentTitle}\n{media.title} " \
                                      f"(S{media.parentIndex}E{media.index})"
                    base_info_layer(embed, media)
                    embed.color = 0x00ff00
                    embed.set_footer(text=f"Located in {library.title}, Plex ID: {media.ratingKey}")
                msg = self.get_media_event(channel.guild.id, event['itemID']).message
                await msg.edit(content="Media Added", embed=embed)
        elif event['state'] == 9:  # Media deleted
            if event in self.event_tracker[channel.guild.id]:
                msg = self.get_media_event(channel.guild.id, event['itemID']).message
                title = self.get_media_event(channel.guild.id, event['itemID']).title
                embed.title = "New Media Added"
                embed.color = 0x00ff00
                library = plex.library.sectionByID(int(event['sectionID']))
                embed.description = f"Media `{title}` deleted from `{library.title}`"
                embed.set_footer(text=f"Media is being merged into another item, media ID: {event['itemID']}")
                await msg.edit(embed=embed)
                return

            library = plex.library.sectionByID(int(event['sectionID']))
            embed.title = "Media Deleted"
            embed.color = 0xff0000
            embed.description = f"Media {event['title']} deleted from {library.title}"
            embed.set_footer(text=f"Media deleted")
            await channel.send(embed=embed)

    @has_permissions(manage_guild=True)
    @command(name="config_event_listener", aliases=["sel"])
    async def config_event_listener(self, ctx, channel: discord.TextChannel = None):
        table = self.bot.database.get_table("plex_alert_channel")
        table.update_or_add(guild_id=ctx.guild.id, channel_id=channel.id)
        await ctx.send("Started event listener")
        # actually start the event listener
        task = self.bot.loop.create_task(self.start_event_listener(ctx.guild.id, channel.id))
        self.listener_tasks[ctx.guild.id] = task

    @has_permissions(manage_guild=True)
    @command(name="stop_event_listener", aliases=["stel"])
    async def stop_event_listener(self, ctx):
        table = self.bot.database.get_table("plex_alert_channel")
        table.delete(guild_id=ctx.guild.id)
        # actually stop the event listener
        task = self.listener_tasks[ctx.guild.id]
        task.cancel()
        await ctx.send("Stopped event listener")

    @has_permissions(manage_guild=True)
    @command(name="event_listener_status", aliases=["els"])
    async def event_listener_status(self, ctx):
        table = self.bot.database.get_table("plex_alert_channel")
        config = table.get_row(guild_id=ctx.guild.id)
        if config is None:
            await ctx.send("No event listener configured")
        else:
            # Check the if the task is still running
            task = self.listener_tasks[ctx.guild.id]
            if task.done():
                await ctx.send("Event listener is configured for channel "
                               f"<#{config['channel_id']}> but is not running")
            else:
                await ctx.send("Event listener is configured for channel "
                               f"<#{config['channel_id']}> and is running")


async def setup(bot):
    await bot.add_cog(PlexEvents(bot))