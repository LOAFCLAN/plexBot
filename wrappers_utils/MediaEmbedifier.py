import asyncio
import datetime
import typing

import aiohttp
import discord
import humanize
import plexapi
from discord import ButtonStyle, Interaction
from discord.ui import View, Button, Select
from plexapi.sync import VIDEO_QUALITY_12_MBPS_1080p

from utils import stringify, get_series_duration, base_info_layer, get_watch_time, get_session_count, safe_field, \
    rating_str, cleanup_url, get_series_size, get_all_library, get_from_media_index
from loguru import logger as logging

from wrappers_utils.Modals import ReviewModal


class PlexSearchView(View):

    def __init__(self, user, plex_search, context, mode: str, results: typing.List, current_content=None):
        super().__init__(timeout=60)
        self.target_message = None
        self.user = user
        self.mode = mode
        self.plex_search = plex_search
        self.results = results
        self.context = context
        self.client = self.plex_search.bot
        self.current_content = current_content
        if mode != "content":
            self.generate_search_select()
        if mode != "search":
            row = self.client.database.get_table("plex_watched_media").get_row(guild_id=user.guild.id,
                                                                               media_guid=self.current_content.guid)
            if row:
                self.add_item(Button(label="Add Review", style=ButtonStyle.grey, custom_id="add_review", emoji="📝"))
            else:
                self.add_item(Button(label="Add Review", style=ButtonStyle.grey, custom_id="add_review", emoji="📝",
                                     disabled=True))
            if current_content.type in ["movie", "episode"]:
                already_optimized = False
                for media in current_content.media:
                    if media.optimizedForStreaming:
                        already_optimized = True
                if already_optimized:
                    self.add_item(Button(label="Transcode", style=ButtonStyle.green, custom_id="optimize",
                                         disabled=True))
                else:
                    self.add_item(Button(label="Transcode", style=ButtonStyle.green, custom_id="optimize"))
        self.add_item(Button(label="Cancel", style=ButtonStyle.red, custom_id="cancel"))
        self.timeout_task = self.client.loop.create_task(self.timeout_task())

    async def timeout_task(self):
        """After 5 minutes, the view will timeout and disable all buttons"""
        try:
            await self.wait()
            # Set all items to disabled, but don't remove them
            for item in self.children:
                item.disabled = True
            await self.target_message.edit(view=self)
        except Exception as e:
            logging.error(f"Error in timeout task: {e}")
            logging.exception(e)

    def set_message(self, message):
        self.target_message = message

    async def interaction_check(self, interaction: Interaction):
        if interaction.user.id == self.user.id:
            await self.process_interaction(interaction)
            return None
        else:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return False

    async def process_interaction(self, interaction: Interaction):
        if interaction.data["custom_id"] == "cancel":
            await interaction.response.defer()
            self.stop()
        elif interaction.data["custom_id"] == "add_review":
            row = self.client.database.get_table("plex_watched_media").get_row(guild_id=interaction.guild.id,
                                                                               media_guid=self.current_content.guid)
            if row:
                await interaction.response.send_modal(ReviewModal(row["media_id"]))
            else:
                await interaction.response.send_message("This media has not been watched yet", ephemeral=True)
        elif interaction.data["custom_id"] == "optimize":
            await interaction.response.defer()
            self.stop()
            await self.optimize_media(self.current_content, interaction)
        else:
            await interaction.response.defer()
            self.stop()
            await interaction.message.edit(content=f"Loading... `{self.results[int(interaction.data['values'][0])]}`",
                                           embed=None, view=None)
            embed, view = await media_details(content=self.results[int(interaction.data["values"][0])],
                                              ctx=self.context,
                                              self=self.plex_search,
                                              requester=interaction.user)
            # Set the target message to the new message
            await asyncio.sleep(0.5)
            view.set_message(await interaction.message.edit(content=None, embed=embed, view=view))

    def get_transcode_session(self, background_task):
        task_key = background_task.key.replace("/transcode/sessions/", "")
        transcode_sessions = self.context.plex.transcodeSessions()
        for session in transcode_sessions:
            if session.key == task_key:
                return session
        return None

    async def update_optimization_message(self, msg, media, media_id):
        # Find the optimization task
        background_tasks = self.context.plex.backgroundSessions()
        while len(background_tasks) > 0:
            background_tasks = self.context.plex.backgroundSessions()
            for task in background_tasks:
                if int(task.ratingKey) == int(media_id):
                    transcode_session = self.get_transcode_session(task)
                    progress = float(task.progress) if task.progress is not None else 0.0
                    embed = discord.Embed(title="Optimize Media",
                                          description=f"Transcoding `{media.title} ({media.year})`",
                                          color=0x00ff00)
                    embed.add_field(name="Progress", value=f"{progress:.2f}%", inline=True)
                    embed.add_field(name="Speed", value=f"{transcode_session.speed:.2f}x" if transcode_session else "N/A", inline=True)
                    embed.add_field(name="Size", value=f"{humanize.naturalsize(transcode_session.size)}" if transcode_session else "N/A",
                                    inline=True)
                    if transcode_session and transcode_session.speed > 0:
                        remaining_media = transcode_session.duration - (transcode_session.duration * (progress / 100))
                        remaining_time = remaining_media / (transcode_session.speed / 100)
                        remaining_time /= 1000  # Convert from ms to s
                    else:
                        remaining_time = -1
                    embed.add_field(name="Estimated Time Remaining",
                                    value=f"{str(datetime.timedelta(seconds=int(remaining_time)))}" if remaining_time > 0 else "N/A",
                                    inline=False)
                    embed.timestamp = datetime.datetime.now()
                    await msg.edit(embed=embed)
            await asyncio.sleep(5)
        embed = discord.Embed(title="Optimize Media",
                              description=f"Optimization task for `{media.title} ({media.year})` completed",
                              color=0x00ff00)
        embed.timestamp = datetime.datetime.now()
        await msg.edit(embed=embed)

    async def optimize_media(self, media, interaction: Interaction):
        """Optimize media for streaming"""
        optimized_items = self.context.plex.optimizedItems()
        in_progress_items = self.context.plex.backgroundSessions()
        print(optimized_items)
        if any(int(item.id) == int(media.ratingKey) for item in optimized_items):
            embed = discord.Embed(title="Optimize Media",
                                  description=f"Media `{media.title} ({media.year})` is already optimized.",
                                  color=0xff0000)
            embed.timestamp = datetime.datetime.now()
            await interaction.followup.send(embed=embed)
            return
        if any(int(item.ratingKey) == int(media.ratingKey) for item in in_progress_items):
            embed = discord.Embed(title="Optimize Media",
                                  description=f"Media `{media.title} ({media.year})` is already being optimized.",
                                  color=0xff0000)
            embed.timestamp = datetime.datetime.now()
            msg = await interaction.followup.send(embed=embed)
            await asyncio.sleep(1)
            await self.update_optimization_message(msg, media, media.ratingKey)
            return
        embed = discord.Embed(title="Optimize Media", description=f"Searching for media: {media.title}", color=0x00ff00)
        embed.timestamp = datetime.datetime.now()
        msg = await interaction.followup.send(embed=embed)
        device_profile = "Android"
        media.optimize(deviceProfile=device_profile, videoQuality=VIDEO_QUALITY_12_MBPS_1080p)
        embed = discord.Embed(title="Optimize Media",
                              description=f"Optimization task started for `{media.title} ({media.year})`",
                              color=0x00ff00)
        embed.timestamp = datetime.datetime.now()
        await msg.edit(embed=embed)
        await asyncio.sleep(1)
        await self.update_optimization_message(msg, media, media.ratingKey)
        embed = discord.Embed(title="Optimize Media",
                              description=f"Optimization task for `{media.title} ({media.year})` completed",
                              color=0x00ff00)
        embed.timestamp = datetime.datetime.now()
        await msg.edit(embed=embed)
        return

    def generate_search_select(self):
        """Generates the search select menu"""
        # Chunk the results into 25 items per select menu
        chunks = [self.results[i:i + 25] for i in range(0, len(self.results), 25)]
        for i, chunk in enumerate(chunks):
            select_thing = Select(custom_id=f"search_select_{i}",
                                  placeholder="Select a result" if len(
                                      chunks) == 1 else f"Select a result ({i + 1}/{len(chunks)})",
                                  min_values=1, max_values=1)
            # Remove any duplicates
            labels = []
            for result in chunk:
                if result.type == "movie":
                    label = f"{result.title} ({result.year})"
                elif result.type == "show":
                    label = f"{result.title} ({result.year})"
                elif result.type == "season":
                    label = f"Season {result.index}"
                elif result.type == "episode":
                    label = f"Episode {result.index} - {result.title}"
                else:
                    label = result.title
                if label not in labels:
                    labels.append(label)
                    if result.type == "season":
                        select_thing.add_option(label=label, value=str(self.results.index(result)),
                                                description=f"Episodes: {len(result.episodes())}")
                    else:
                        select_thing.add_option(label=label, value=str(self.results.index(result)))
                else:
                    logging.info(f"Duplicate result found: {label}")
            self.add_item(select_thing)


async def media_details(content, self=None, ctx=None, requester=None, full=True):
    """Show details about a content"""
    view = None

    if content.isPartialObject():  # For some reason plex likes to not give everything we asked for
        content.reload()  # So if plex is being a jerk, we'll reload the content

    if isinstance(content, plexapi.video.Movie):
        """Format the embed being sent for a movie"""
        embed = discord.Embed(title=f"{content.title} ({content.year})",
                              description=f"{content.tagline if content.tagline else 'No Tagline'}", color=0x00ff00)
        if full:
            embed.add_field(name="Summary", value=content.summary, inline=False)

        base_info_layer(embed, content, database=self.bot.database, full=full)
        if self and requester:
            view = PlexSearchView(requester, self, ctx, "content", content, content)

    elif isinstance(content, plexapi.video.Show):  # ----------------------------------------------------------
        """Format the embed being sent for a show"""

        rating_string = rating_str(content, database=self.bot.database)

        embed = discord.Embed(title=f"{safe_field(content.title)}",
                              description=f"{content.tagline if content.tagline else 'No Tagline'}", color=0x00ff00)
        embed.add_field(name="Summary", value=safe_field(content.summary), inline=False)
        embed.add_field(name="Rating", value=rating_string, inline=False)
        embed.add_field(name="Genres", value=stringify(content.genres), inline=False)

        if content.studio:
            embed.add_field(name="Studio", value=content.studio, inline=True)
        elif content.network:
            embed.add_field(name="Network", value=content.network, inline=True)
        else:
            embed.add_field(name="Studio", value="Unknown", inline=True)
        size = await self.bot.loop.run_in_executor(None, get_series_size, content)
        embed.add_field(name="Size", value=humanize.naturalsize(size), inline=True)
        embed.add_field(name="Originally Aired", value=content.originallyAvailableAt.strftime("%B %d, %Y"),
                        inline=True)

        embed.add_field(name="Average Episode Runtime",
                        value=f"{datetime.timedelta(milliseconds=content.duration)}", inline=True)
        embed.add_field(name="Total Duration",
                        value=f"{datetime.timedelta(seconds=round(get_series_duration(content) / 1000))}",
                        inline=True)
        embed.add_field(name="Watch Time", value=f"{get_watch_time(content, self.bot.database)}", inline=True)
        embed.add_field(name="Total Seasons", value=content.childCount, inline=True)
        embed.add_field(name="Total Episodes", value=f"{len(content.episodes())}", inline=True)
        count = get_session_count(content, self.bot.database)
        embed.add_field(name="Total Sessions",
                        value=f"{'No sessions' if count == 0 else ('Not Available' if count == -1 else count)}",
                        inline=True)
        if self and requester:
            view = PlexSearchView(requester, self, ctx, "season", content.seasons(), content)

    elif isinstance(content, plexapi.video.Season):  # ------------------------------------------------------
        """Format the embed being sent for a season"""
        embed = discord.Embed(title=f"{content.parentTitle}",
                              description=f"Season {content.index}", color=0x00ff00)
        embed.add_field(name=f"Episodes: {len(content.episodes())}",
                        value=stringify(content.episodes(), separator="\n")[:1024], inline=False)
        embed.add_field(name="Total Duration",
                        value=f"{datetime.timedelta(seconds=round(get_series_duration(content) / 1000))}",
                        inline=True)
        embed.add_field(name="Watch Time", value=f"{get_watch_time(content, self.bot.database)}", inline=True)
        embed.add_field(name="Total Size", value=humanize.naturalsize(get_series_size(content)), inline=True)
        if self and requester:
            view = PlexSearchView(requester, self, ctx, "episode", content.episodes(), content)

    elif isinstance(content, plexapi.video.Episode):  # ------------------------------------------------------
        """Format the embed being sent for an episode"""
        embed = discord.Embed(title=f"{content.grandparentTitle}\n{content.title} "
                                    f"(S{content.parentIndex}E{content.index})",
                              description=f"{content.summary}" if full else "", color=0x00ff00)
        base_info_layer(embed, content, database=self.bot.database, full=full)
        if self and requester:
            view = PlexSearchView(requester, self, ctx, "content", content, content)
    else:
        embed = discord.Embed(title="Unknown content type", color=0x00ff00)

    ###############################################################################################################

    db_entry = self.bot.database.get_table("plex_watched_media").get_row(media_guid=content.guid)

    # if inter is not None:
    #     await inter.disable_components()

    if hasattr(content, "thumb"):
        thumb_url = cleanup_url(content.thumb)
        # Validate that there is an image hosted at the URL by trying to open it
        # noinspection PyBroadException
        try:
            # Check if a file is hosted at the URL
            async with aiohttp.ClientSession() as session:
                async with session.get(thumb_url) as r:
                    if r.status != 200:
                        logging.warning(f"Bad thumb URL: {thumb_url} - {r.status}")
                        thumb_url = "https://cdn.discordapp.com/attachments/1191806535861538948/1191806693621911572/bad_thumb.png"
        except Exception as e:
            logging.warning(f"Error validating thumb URL: {thumb_url} - {e}")
            thumb_url = "https://cdn.discordapp.com/attachments/1191806535861538948/1191806693621911572/bad_thumb.png"
        embed.set_thumbnail(url=thumb_url)

    # embed.set_footer(text=f"{content.guid}", icon_url=requester.avatar_url)
    if requester:
        embed.set_author(name=f"Requested by: {requester.display_name}", icon_url=requester.display_avatar.url)

    embed.set_footer(text=f"Located in {content.librarySectionTitle}, "
                          f"Media ID: {db_entry['media_id'] if db_entry else 'N/A'}, "
                          f"Plex ID: {content.ratingKey}")

    return embed, view
