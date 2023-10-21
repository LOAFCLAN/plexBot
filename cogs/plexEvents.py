import asyncio
import datetime
import requests
import os

import discord
import plexapi
from discord.ext.commands import command, has_permissions, Cog, BadArgument, is_owner
from loguru import logger as logging

from utils import get_from_media_index, safe_field, base_info_layer, rating_str, stringify, get_series_duration, \
    get_watch_time, get_session_count, cleanup_url
from wrappers_utils import EventDecorator
from wrappers_utils.EventDecorator import EventManager, event_manager
from wrappers_utils.MediaEmbedifier import media_details


class PlexEvents(Cog):
    class PlexMediaEvent:

        def __init__(self, itemID, message):
            self.message = message
            self.itemID = itemID

            self.title = "This media has no title and this should never be matched"
            self.last_state = 0

        def __eq__(self, other):
            if isinstance(other, PlexEvents.PlexMediaEvent):
                return self.itemID == other.itemID
            else:
                return other['itemID'] == self.itemID or other['title'] == self.title

    def __init__(self, bot):
        self.bot = bot
        table = self.bot.database.get_table("plex_alert_channel")
        self.plex_alert_channels = table.get_all()
        self.event_message_table = self.bot.database.get_table("plex_media_event_messages")
        event_manager.add_instance(self)

        self.event_tracker = {}
        self.listener_tasks = {}

    @Cog.listener()
    async def on_ready(self):
        logging.info("PlexEvents is ready")
        # for entry in self.plex_alert_channels:
        #     self.bot.loop.create_task(self.start_event_listener(entry[0], entry[1]))

    @EventDecorator.on_event('plex_connect')
    async def on_plex_connect(self, plex):
        """Called when the bot establishes a connection a plex server"""
        # Used to start the event listener
        guild = plex.host_guild
        table = self.bot.database.get_table("plex_alert_channel")
        if table.get_row(guild_id=guild.id) is None:
            return
        channel_id = table.get_row(guild_id=guild.id)['channel_id']
        logging.debug(f"Starting event listener for {guild.name} ({plex.friendlyName})")
        self.bot.loop.create_task(self.start_event_listener(guild.id, channel_id))

    @EventDecorator.on_event('plex_disconnect')
    async def on_plex_disconnect(self, plex):
        """Called when the bot disconnects from a plex server"""
        guild = plex.host_guild
        if guild.id in self.listener_tasks:
            task = self.listener_tasks[guild.id]
            task.cancel()
            del self.listener_tasks[guild.id]
            logging.info(f"Stopped event listener for {guild.name}")

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
        embed = discord.Embed(title="Plex Event Listener Failure",
                                description=f"The event listener for {guild.name} has stopped. "
                                            f"Attempting to restart the event listener",
                                color=discord.Color.red())
        await channel.send(embed=embed)
        # Start the event listener again
        task.cancel()
        await self.start_event_listener(guild_id, channel_id)

    def event_error(self, error):
        logging.error(error)
        logging.exception(error)

    async def event_message_loop(self, plex, queue, channel):
        self.event_tracker[channel.guild.id] = []
        lock = asyncio.Lock()
        while True:
            try:
                event = await queue.get()  # type: dict
                if event is None:
                    break
                if int(event['sectionID']) == -1:
                    continue
                # print(event)
                await lock.acquire()  # Ensure only one event is processed at a time
                await self.send_event_message(plex, channel, event)
                lock.release()
            except Exception as e:
                logging.error(e)
                logging.exception(e)

    def get_media_event(self, guild_id, itemID, title=None):
        for event in self.event_tracker[guild_id]:
            if event.itemID == itemID or event.title == title:
                return event
        return None

    async def safe_mediaID_search(self, library, mediaID):
        """Because searching by mediaID is blocking and takes awhile we need to do it asyncio safe to prevent
        blocking the event loop"""
        # Use get_from_media_index(library, mediaID)
        return await self.bot.loop.run_in_executor(None, get_from_media_index, library, mediaID)

    async def get_message_from_plex_id(self, plex_media_id):
        """Searches the database for a message with the given itemID"""
        table = self.bot.database.get_table("plex_media_event_messages")
        row = table.get_row(plex_media_id=plex_media_id)
        if row is None:
            return None
        channel = self.bot.get_channel(row['channel_id'])
        message = await channel.fetch_message(row['message_id'])
        return message

    async def apply_media_info(self, channel, event_obj=None, library=None, edit=False, media_obj=None, msg=None):
        """Applies the media info to the message"""
        embed = discord.Embed()
        if media_obj is None:
            media = await self.safe_mediaID_search(library, event_obj.itemID)
        else:
            media = media_obj
        if media is not None:
            # If media is an episode update the series and season embeds that were sent previously
            if media.type == 'episode':
                series_message = await self.get_message_from_plex_id(media.grandparentRatingKey)
                if series_message is not None:
                    await self.apply_media_info(channel, library=library, edit=True, media_obj=media.show(),
                                                msg=series_message)
                else:
                    logging.warning(f"Could not find series message for {media.grandparentTitle}")
                season_message = await self.get_message_from_plex_id(media.parentRatingKey)
                if season_message is not None:
                    await self.apply_media_info(channel, library=library, edit=True, media_obj=media.season(),
                                                msg=season_message)
                else:
                    logging.warning(f"Could not find season message for {media.parentTitle}")
            # Save the message info for the season
            self.event_message_table.update_or_add(plex_media_id=media.ratingKey, guild_id=channel.guild.id,
                                                   channel_id=channel.id,
                                                   message_id=event_obj.message.id if event_obj is not None else msg.id)
            try:
                await self.download_thumbnails(channel, media)
            except Exception as e:
                logging.error(e)
                logging.exception(e)
            embed, view = await media_details(media, self, full=False)

        if not edit:
            await event_obj.message.edit(content="Media Added", embed=embed)
            self.event_tracker[channel.guild.id].remove(event_obj)
        else:
            await msg.edit(embed=embed)

    async def download_thumbnails(self, channel, media):
        """Downloads thumbnails for media into the webserver path"""
        server_info = self.bot.database.get_table("plex_servers").get_row(guild_id=channel.guild.id)
        if not server_info['webserver_path']:
            return
        urls, paths = [], []
        if media.posterUrl:
            urls.append(media.posterUrl)
        if media.artUrl:
            urls.append(media.artUrl)
        if media.thumbUrl:
            urls.append(media.thumbUrl)
        plex_url = server_info['server_url']
        paths = [os.path.join(server_info['webserver_path'], url[len(plex_url) + 1:url.find('?')] + ".jpg")
                 for url in urls]
        for i in range(len(urls)):
            if not os.path.exists(paths[i]):
                os.makedirs(paths[i][:paths[i].rfind('/')], exist_ok=True)
                r = requests.get(urls[i], timeout=5)
                with open(paths[i], 'wb') as f:
                    f.write(r.content)

    async def send_event_message(self, plex, channel, event):

        # only include messages with an ID of 0, 5, 9
        match event['state']:
            case 0:
                library = plex.library.sectionByID(int(event['sectionID']))
                embed = discord.Embed(title="New Media Added", color=0x00FFFF,
                                      description=f"New media file added to `{library.title}`")
                embed.set_footer(text=f"Waiting for item matching to complete")
                msg = await channel.send(embed=embed)
                await asyncio.sleep(1)
                self.event_tracker[channel.guild.id].append(self.PlexMediaEvent(event['itemID'], msg))
            case 1:
                if event in self.event_tracker[channel.guild.id]:
                    event_obj = self.get_media_event(channel.guild.id, event['itemID'])
                    if event_obj.last_state == 1:
                        return
                    event_obj.title = event['title']
                    library = plex.library.sectionByID(int(event['sectionID']))
                    embed = discord.Embed(title="New Media Added", color=0x00FFFF,
                                          description=f"Media `{event['title']}` added to `{library.title}`")
                    embed.set_footer(text=f"Waiting for metadata download to start, media ID: {event['itemID']}")
                    await event_obj.message.edit(embed=embed)
                    await asyncio.sleep(1)
                    event_obj.last_state = 1
            case 3:
                if event in self.event_tracker[channel.guild.id]:
                    event_obj = self.get_media_event(channel.guild.id, event['itemID'], event['title'])
                    if event_obj.last_state == 3:
                        return
                    event_obj.itemID = event['itemID']
                    library = plex.library.sectionByID(int(event['sectionID']))
                    embed = discord.Embed(title="New Media Added", color=0x00FFFF,
                                          description=f"Media `{event['title']}` added to `{library.title}`")
                    embed.set_footer(text=f"Waiting for metadata download to finish, media ID: {event['itemID']}")
                    msg = self.get_media_event(channel.guild.id, event['itemID']).message
                    await msg.edit(embed=embed)
                    await asyncio.sleep(1)
                    event_obj.last_state = 3
            case 4:
                if event in self.event_tracker[channel.guild.id]:
                    event_obj = self.get_media_event(channel.guild.id, event['itemID'], event['title'])
                    if event_obj.last_state == 4:
                        return
                    event_obj.itemID = event['itemID']
                    library = plex.library.sectionByID(int(event['sectionID']))
                    embed = discord.Embed(title="New Media Added", color=0x00FFFF,
                                          description=f"Media `{event['title']}` added to `{library.title}`")
                    embed.set_footer(text=f"Processing media metadata, media ID: {event['itemID']}")
                    msg = self.get_media_event(channel.guild.id, event['itemID']).message
                    await msg.edit(embed=embed)
                    await asyncio.sleep(1)
                    event_obj.last_state = 4
            case 5:
                if event in self.event_tracker[channel.guild.id]:
                    library = plex.library.sectionByID(int(event['sectionID']))
                    event_obj = self.get_media_event(channel.guild.id, event['itemID'])
                    if event_obj.last_state == 5:
                        return
                    embed = discord.Embed(title="New Media Added", color=0x00FFFF,
                                          description=f"Searching for media `{event['title']}` in `{library.title}`")
                    embed.set_footer(text=f"Metadata download complete searching for media ID: {event['itemID']}")
                    await event_obj.message.edit(embed=embed)
                    # Start a new task to search for the media
                    asyncio.ensure_future(self.apply_media_info(channel, event_obj, library))
                    event_obj.last_state = 5
                    await asyncio.sleep(1)
            case 9:
                if event in self.event_tracker[channel.guild.id]:
                    msg = self.get_media_event(channel.guild.id, event['itemID']).message
                    title = self.get_media_event(channel.guild.id, event['itemID']).title
                    library = plex.library.sectionByID(int(event['sectionID']))
                    embed = discord.Embed(title="New Media Added", color=0x00FFFF,
                                          description=f"Merging media `{title}` in `{library.title}`")
                    embed.set_footer(text=f"Media ID: {event['itemID']}")
                    await msg.edit(embed=embed)
                    await asyncio.sleep(1)
                    return

                # Find the original media message if it exists
                message = await self.get_message_from_plex_id(event['itemID'])
                library = plex.library.sectionByID(int(event['sectionID']))
                embed = discord.Embed(title="Media Deleted", color=0xff0000,
                                      description=f"Media `{event['title']}` deleted from `{library.title}`")
                embed.set_footer(text=f"Media ID: {event['itemID']}")
                if message:
                    await channel.send(embed=embed, reference=message)
                    # Edit the text of the original message indicate the media was deleted but leave the embed intact
                    await message.edit(content="`This media has been deleted`", embed=message.embeds[0])
                else:
                    await channel.send(embed=embed)
                await asyncio.sleep(1)

    @has_permissions(manage_guild=True)
    @command(name="config_event_listener", aliases=["sel"])
    async def config_event_listener(self, ctx, channel: discord.TextChannel = None):
        table = self.bot.database.get_table("plex_alert_channel")
        table.update_or_add(guild_id=ctx.guild.id, channel_id=channel.id)
        # Stop any existing event listeners
        if ctx.guild.id in self.listener_tasks:
            self.listener_tasks[ctx.guild.id].cancel()
            await ctx.send("Stopped existing event listener")
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

    @is_owner()
    @command(name="set_webserver_path")
    async def set_webserver_path(self, ctx, path):
        table = self.bot.database.get_table("plex_servers")
        table.update_or_add(guild_id=ctx.guild.id, webserver_path=path)
        await ctx.send("Updated webserver path")


async def setup(bot):
    await bot.add_cog(PlexEvents(bot))
