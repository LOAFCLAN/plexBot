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

    async def media_details(self, content) -> discord.Embed:
        """Gets the details of the media"""
        if content.isPartialObject():  # For some reason plex likes to not give everything we asked for
            content.reload()  # So if plex is being a jerk, we'll reload the content

        if isinstance(content, plexapi.video.Movie):
            """Format the embed being sent for a movie"""
            embed = discord.Embed(title=f"{content.title} ({content.year})",
                                  description=f"{content.tagline if content.tagline else 'No Tagline'}", color=0x00ff00)
            embed.add_field(name="Summary", value=content.summary, inline=False)

            base_info_layer(embed, content, database=self.bot.database)

        elif isinstance(content, plexapi.video.Show):  # ----------------------------------------------------------
            """Format the embed being sent for a show"""

            rating_string = rating_str(content, database=self.bot.database)

            embed = discord.Embed(title=f"{safe_field(content.title)}",
                                  description=f"{content.tagline if content.tagline else 'No Tagline'}", color=0x00ff00)
            embed.add_field(name="Summary", value=safe_field(content.summary), inline=False)
            embed.add_field(name="Rating", value=rating_string, inline=False)
            embed.add_field(name="Genres", value=stringify(content.genres), inline=False)

            embed.add_field(name="Studio", value=content.studio, inline=True)
            embed.add_field(name="Network", value=content.network, inline=True)
            embed.add_field(name="Originally Aired", value=content.originallyAvailableAt.strftime("%B %d, %Y"),
                            inline=True)

            embed.add_field(name="Average Episode Runtime",
                            value=f"{datetime.timedelta(milliseconds=content.duration)}", inline=True)
            embed.add_field(name="Total Duration",
                            value=f"{datetime.timedelta(seconds=round(get_series_duration(content) / 1000))}",
                            inline=True)
            embed.add_field(name="Watch Time", value=f"{get_watch_time(content, self.bot.database)}", inline=True)
            embed.add_field(name="Total Season", value=content.childCount, inline=True)
            embed.add_field(name="Total Episodes", value=f"{len(content.episodes())}", inline=True)
            embed.add_field(name="Total Sessions", value=f"{get_session_count(content, self.bot.database)}",
                            inline=True)

        elif isinstance(content, plexapi.video.Season):  # ------------------------------------------------------
            """Format the embed being sent for a season"""
            embed = discord.Embed(title=f"{content.parentTitle}",
                                  description=f"Season {content.index}", color=0x00ff00)
            embed.add_field(name=f"Episodes: {len(content.episodes())}",
                            value=stringify(content.episodes(), separator="\n")[:1024], inline=False)
            embed.add_field(name="Total Duration",
                            value=f"{datetime.timedelta(seconds=round(get_series_duration(content) / 1000))}",
                            inline=True)

        elif isinstance(content, plexapi.video.Episode):  # ------------------------------------------------------
            """Format the embed being sent for an episode"""
            embed = discord.Embed(title=f"{content.grandparentTitle}\n{content.title} "
                                        f"(S{content.parentIndex}E{content.index})",
                                  description=f"{content.summary}", color=0x00ff00)
            base_info_layer(embed, content, database=self.bot.database)
        else:
            embed = discord.Embed(title="Unknown content type", color=0x00ff00)

        db_entry = self.bot.database.get_table("plex_watched_media").get_row(media_guid=content.guid)

        # if inter is not None:
        #     await inter.disable_components()

        if hasattr(content, "thumb"):
            thumb_url = cleanup_url(content.thumb)
            embed.set_thumbnail(url=thumb_url)

        embed.set_footer(text=f"Located in {content.librarySectionTitle}, "
                              f"Media ID: {db_entry['media_id'] if db_entry else 'N/A'}, Plex ID: {content.ratingKey}")

        return embed

    async def apply_media_info(self, channel, event_obj, library):
        """Applies the media info to the message"""

        embed = discord.Embed()
        media = await self.safe_mediaID_search(library, event_obj.itemID)
        if media is not None:
            await self.download_thumbnails(channel, media)
            embed = await self.media_details(media)

        await event_obj.message.edit(content="Media Added", embed=embed)
        self.event_tracker[channel.guild.id].remove(event_obj)

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
        embed = discord.Embed()

        # only include messages with an ID of 0, 5, 9
        if event['state'] == 0:
            embed.title = "New Media Added"
            embed.color = 0x00ff00
            library = plex.library.sectionByID(int(event['sectionID']))
            embed.description = f"New media file added to {library.title}"
            embed.set_footer(text=f"Waiting for item matching to complete")
            msg = await channel.send(embed=embed)
            await asyncio.sleep(1)
            self.event_tracker[channel.guild.id].append(self.PlexMediaEvent(event['itemID'], msg))
        elif event['state'] == 1:  # Matching item
            if event in self.event_tracker[channel.guild.id]:
                event_obj = self.get_media_event(channel.guild.id, event['itemID'])
                if event_obj.last_state != 0:
                    return
                event_obj.title = event['title']
                embed.title = "New Media Added"
                embed.color = 0x00ff00
                library = plex.library.sectionByID(int(event['sectionID']))
                embed.description = f"Media `{event['title']}` added to `{library.title}`"
                embed.set_footer(text=f"Waiting for metadata download to start, media ID: {event['itemID']}")
                await event_obj.message.edit(embed=embed)
                await asyncio.sleep(1)
                event_obj.last_state = 1
        elif event['state'] == 3:  # metadata download started
            if event in self.event_tracker[channel.guild.id]:
                event_obj = self.get_media_event(channel.guild.id, event['itemID'], event['title'])
                if event_obj.last_state != 1:
                    return
                event_obj.itemID = event['itemID']
                embed.title = "New Media Added"
                embed.color = 0x00ff00
                library = plex.library.sectionByID(int(event['sectionID']))
                embed.description = f"Media `{event['title']}` added to `{library.title}`"
                embed.set_footer(text=f"Waiting for metadata download to finish, media ID: {event['itemID']}")
                msg = self.get_media_event(channel.guild.id, event['itemID']).message
                await msg.edit(embed=embed)
                await asyncio.sleep(1)
                event_obj.last_state = 3
        elif event['state'] == 4:  # metadata download finished
            if event in self.event_tracker[channel.guild.id]:
                event_obj = self.get_media_event(channel.guild.id, event['itemID'], event['title'])
                if event_obj.last_state != 3 and event_obj.last_state != 1:
                    return
                event_obj.itemID = event['itemID']
                embed.title = "New Media Added"
                embed.color = 0x00ff00
                library = plex.library.sectionByID(int(event['sectionID']))
                embed.description = f"Media `{event['title']}` added to `{library.title}`"
                embed.set_footer(text=f"Processing media metadata, media ID: {event['itemID']}")
                msg = self.get_media_event(channel.guild.id, event['itemID']).message
                await msg.edit(embed=embed)
                await asyncio.sleep(1)
                event_obj.last_state = 4
        elif event['state'] == 5:
            if event in self.event_tracker[channel.guild.id]:
                library = plex.library.sectionByID(int(event['sectionID']))
                event_obj = self.get_media_event(channel.guild.id, event['itemID'])
                if event_obj.last_state != 4:
                    return
                embed.title = "New Media Added"
                embed.description = f"Searching for `{event['title']}` in `{library.title}`"
                embed.set_footer(text=f"Metadata download complete searching for media ID: {event['itemID']}")
                await event_obj.message.edit(embed=embed)
                # Start a new task to search for the media
                asyncio.ensure_future(self.apply_media_info(channel, event_obj, library))
                event_obj.last_state = 5
                await asyncio.sleep(1)
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
                await asyncio.sleep(1)
                return

            library = plex.library.sectionByID(int(event['sectionID']))
            embed.title = "Media Deleted"
            embed.color = 0xff0000
            embed.description = f"Media {event['title']} deleted from {library.title}"
            embed.set_footer(text=f"Media deleted")
            await channel.send(embed=embed)
            await asyncio.sleep(1)

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

    @is_owner()
    @command(name="set_webserver_path")
    async def set_webserver_path(self, ctx, path):
        table = self.bot.database.get_table("plex_servers")
        table.update_or_add(guild_id=ctx.guild.id, webserver_path=path)
        await ctx.send("Updated webserver path")


async def setup(bot):
    await bot.add_cog(PlexEvents(bot))
