import asyncio
import datetime
import random
import traceback

import discord
import plexapi.video
from discord import Interaction, ButtonStyle, ActionRow
from discord.ext import commands
from discord.ext.commands import Cog, command, has_permissions
# import custom_dpy_overrides
# from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction, ActionRow
from discord.ui import Button, View, Select

from wrappers_utils.BotExceptions import PlexNotLinked, PlexNotReachable
from wrappers_utils.SessionChangeWatchers import SessionChangeWatcher, SessionWatcher
from utils import base_info_layer, get_season, get_episode, cleanup_url, text_progress_bar_maker, stringify, \
    base_user_layer, get_series_duration, get_from_guid

from loguru import logger as logging


def hash_media_event(media) -> int:
    """Hash a media watch event, so we can easily reference it later
    The hash is based on the medias title, guid, userID of the watcher and the viewedAt
    """
    return hash(hash(media) + hash(datetime.datetime.now()))


class PlexHistory(commands.Cog):

    class ReviewModal(discord.ui.Modal):

        review_value = discord.ui.TextInput(label="Score", style=discord.TextStyle.short, min_length=1, max_length=3)

        def __init__(self, media_id, *, timeout=None):
            super().__init__(title="Media Review", timeout=timeout)
            self.media_id = media_id

        async def on_submit(self, interaction: discord.Interaction):  # pylint: disable=arguments-differ
            """Handles when a modal is submitted"""
            review = self.review_value.value
            if not review.isdigit():
                await interaction.response.send_message("Score must be a number", ephemeral=True)
                return
            review = int(review)
            if review < 0 or review > 100:
                await interaction.response.send_message("Score must be between 0 and 100", ephemeral=True)
                return
            logging.info(f"Review: {review}")
            table = interaction.client.database.get_table("plex_afs_ratings")
            row = table.get_row(media_id=self.media_id, user_id=interaction.user.id)
            if row:
                row.set(rating=review)
                await interaction.response.send_message("Review updated", ephemeral=True)
            else:
                table.add(media_id=self.media_id, user_id=interaction.user.id, rating=review)
                await interaction.response.send_message("Review added", ephemeral=True)

    class HistoryOptions(discord.ui.View):

        def __init__(self, *, timeout=None):
            super().__init__(timeout=timeout)

        @discord.ui.button(label="Media Info", style=ButtonStyle.blurple, custom_id="mediainfo",
                           emoji="📹")
        async def media_info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Get the message ID
            event = self.get_event(interaction)
            # Get the media from the database
            media_entry = event.get("plex_watched_media")
            # Get the media object
            if len(media_entry) == 1:
                await interaction.response.defer(thinking=True, ephemeral=True)
                original_response = await interaction.original_response()
                media = await self.media_from_guid(interaction.message.guild, interaction.client,
                                                   media_entry[0], interaction)
                if media:
                    # Get the embed
                    embed = self.media_embed(media, interaction.client.database, media_entry[0]["media_id"])
                    # Send the embed
                    await original_response.edit(embed=embed)
                else:
                    await original_response.edit(content="Media not found")
            else:
                await interaction.response.send_message("Media not found", ephemeral=True)

        @discord.ui.button(label="User Info", style=ButtonStyle.green, custom_id="userinfo",
                           emoji="\N{BUSTS IN SILHOUETTE}")
        async def user_info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            event = self.get_event(interaction)
            if event:
                # Get the user ID
                account_id = event["account_id"]
                guild = interaction.guild
                plex = await interaction.client.fetch_plex(guild)
                user = plex.associations.get(account_id)
                embed = base_user_layer(user, interaction.client.database)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message("PlexBot was unable to find this media event in the database.",
                                                        ephemeral=True)

        @discord.ui.button(label="Add Rating", style=ButtonStyle.grey, custom_id="addrating",
                           emoji="📝")
        async def add_rating_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Verify that the user clicking the button is the same user who watched the media
            # Get the message ID
            event = self.get_event(interaction)
            if event:
                # Get the user ID
                account_id = event["account_id"]
                guild = interaction.guild
                plex = await interaction.client.fetch_plex(guild)
                user = plex.associations.get(account_id)
                if interaction.user.id == user.discord_id:
                    # Get the media from the database
                    media_entry = event.get("plex_watched_media")
                    # Get the media object
                    if len(media_entry) == 1:
                        # Create a popup view
                        await interaction.response.send_modal(PlexHistory.ReviewModal(media_entry[0]["media_id"],
                                                                                      timeout=60))
                    else:
                        await interaction.response.send_message("PlexBot was able to find a watch event but"
                                                                " was unable to find the media entry"
                                                                " associated with it",
                                                                ephemeral=True)
                else:
                    await interaction.response.send_message("You are not the user who watched this media!",
                                                            ephemeral=True)

        def get_event(self, interaction: discord.Interaction):
            # Get the message ID
            message_id = interaction.message.id
            # Get the event from the database
            table = interaction.client.database.get_table("plex_history_messages")
            message = table.get_row(message_id=message_id)
            if message is None:
                return None
            event = message.get("plex_history_events")
            if len(event) == 0:
                return None
            event = event[0]
            return event

        @staticmethod
        async def media_from_guid(guild, client, entry):
            plex = await client.fetch_plex(guild)
            if entry["library_id"] == "N/A" or entry["media_guid"] == "N/A":
                return None
            library = plex.library.sectionByID(int(entry["library_id"]))
            if entry["media_type"] == "episode":
                show_entry = client.database.get_table("plex_watched_media").get_row(media_id=entry["show_id"])
                if show_entry:
                    # tell discord we are thinking
                    show = get_from_guid(library, show_entry["media_guid"])
                    if show:
                        media = show.episode(
                            title=entry["title"], season=int(entry["season_num"]), episode=int(entry["ep_num"]))
                    else:
                        return None
                else:
                    logging.warning(f"Unable to find show with ID {entry['show_id']}")
                    return False
            else:
                media = get_from_guid(library, entry["media_guid"])
            return media

        @staticmethod
        def media_embed(content, database, media_id):

            if content.isPartialObject():  # If the media is only partially loaded
                content.reload()  # do it correctly this time

            if isinstance(content, plexapi.video.Movie):
                embed = discord.Embed(title=f"{content.title} ({content.year})",
                                      description=f"{content.tagline}", color=0x00ff00)
                base_info_layer(embed, content, database=database)  # Add the base info layer to the embed

            elif isinstance(content, plexapi.video.Episode):  # ------------------------------------------------------
                """Format the embed being sent for an episode"""
                embed = discord.Embed(title=f"{content.grandparentTitle}\n{content.title} "
                                            f"(S{content.parentIndex}E{content.index})",
                                      description=f"{content.summary}", color=0x00ff00)
                base_info_layer(embed, content, database=database)

            else:
                embed = discord.Embed(title=f"Unknown media type", color=0x00ff00)

            embed.set_footer(text=f"Located in {content.librarySectionTitle}, "
                                  f"Media ID: {media_id if media_id else 'N/A'}")

            if hasattr(content, "thumb"):
                thumb_url = cleanup_url(content.thumb)
                embed.set_thumbnail(url=thumb_url)

            return embed

    def __init__(self, bot):
        self.bot = bot
        self.msg_cache = {}
        self.cached_history = {}
        self.sent_hashes = []
        self.history_channels = []

    @Cog.listener('on_ready')
    async def on_ready(self):
        logging.info("Cog: PlexHistory is ready")
        table = self.bot.database.get_table("plex_history_channel")
        for row in table.get_all():
            self.msg_cache[row[0]] = {}
            asyncio.get_event_loop().create_task(self.history_watcher(row[0], row[1]))
        logging.info("PlexHistory startup complete")

    @Cog.listener('on_raw_message_delete')
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        # Check if the message was in a history channel
        if payload.channel_id in self.history_channels:
            table = self.bot.database.get_table("plex_history_messages")
            message_entry = table.get_row(message_id=payload.message_id)
            if message_entry:
                history_entry = message_entry.get("plex_history_events")
                if len(history_entry) == 1:
                    # Delete the history entry
                    history_entry[0].delete()
                    table.delete(message_id=payload.message_id)
                    logging.debug(f"Deleted history entry for message {payload.message_id}")
                else:
                    logging.error("Found multiple history entries for a single message")

    async def history_watcher(self, guild_id, channel_id):
        channel = await self.bot.fetch_channel(channel_id)
        self.history_channels.append(channel.id)
        guild = await self.bot.fetch_guild(guild_id)
        try:
            plex = await self.bot.fetch_plex(guild)
        except PlexNotReachable:
            logging.warning(f"Can't start history watcher for {guild.name} because Plex is not reachable")
            return
        except PlexNotLinked:
            logging.warning(f"Can't start history watcher for {guild.name} because Plex is not linked")
            return
        self.bot.session_watchers.append(SessionChangeWatcher(plex, self.on_watched, channel))

    async def on_watched(self, watcher, channel):
        try:
            await self.send_history_message(channel.guild, channel, watcher, await self.bot.fetch_plex(channel.guild))
        except Exception as e:
            logging.error(f"Error sending history message: {e}")
            logging.exception(e)
            await self.send_history_error(channel, e)

    async def send_history_error(self, channel, error):
        embed = discord.Embed(title="Plex History Message Error", description=f"`{error}`", color=0xff0000)
        embed.add_field(name="Traceback", value=f"```{traceback.format_exc()[0:1000]}```")
        await channel.send(embed=embed)

    async def send_history_message(self, guild, channel, watcher: SessionWatcher, plex):

        start_session = watcher.initial_session
        session = watcher.session
        user = plex.associations.get(session.usernames[0])
        # if accountID is None:
        #     username = session.usernames[0]
        #     for user in plex.systemAccounts():
        #         if user.name == username:
        #             accountID = user.accountID
        #             break

        if len(session.players) >= 1:
            device_name = session.players[0].machineIdentifier
            device = None
            for sys_device in plex.systemDevices():
                if sys_device.clientIdentifier == device_name:
                    device = sys_device
                    break
        else:
            device = None

        accountID = user.plex_system_account.accountID if user is not None else None

        time = watcher.start_time

        raw_current_position = watcher.end_offset
        raw_duration = session.duration
        raw_start_position = start_session.viewOffset

        progress_bar = text_progress_bar_maker(raw_duration, raw_current_position, raw_start_position)

        current_position = datetime.timedelta(seconds=round(raw_current_position / 1000))
        duration = datetime.timedelta(seconds=round(raw_duration / 1000))
        start_position = datetime.timedelta(seconds=round(raw_start_position / 1000))
        watched_time = datetime.timedelta(seconds=round(watcher.watch_time))

        # Calculate the amount of content that was skipped based on the start and end positions and the watched time
        if device:
            text = f"{user.mention()} watched this with `{device.name}` on `{device.platform.capitalize()}`\n" \
                   f"They watched `{watched_time}` of `{duration}`\n"
        else:
            text = f"{user.mention()} watched this on an unknown device\n" \
                   f"They watched `{watched_time}` of `{duration}`\n"
        embed = discord.Embed(description=text, color=0x00ff00, timestamp=time)
        if session.type == "episode":
            embed.title = f"{session.title}"
            embed.set_author(name=f"{session.grandparentTitle} - "
                                  f"S{str(session.parentIndex).zfill(2)}E{str(session.index).zfill(2)}",
                             icon_url=user.avatar_url())
        else:
            embed.set_author(name=f"{session.title} ({session.year})", icon_url=user.avatar_url())

        embed.add_field(name=f"Progress: {start_position}->{current_position}",
                        value=progress_bar, inline=False)

        alive_time = datetime.timedelta(seconds=round((datetime.datetime.utcnow()
                                                       - watcher.alive_time).total_seconds()))
        watch_time = watcher.watch_time
        embed.set_footer(text=f"This session was alive for {alive_time}, Started")

        if hasattr(session, "thumb"):
            thumb_url = cleanup_url(session.thumb)
            embed.set_thumbnail(url=thumb_url)

        m_hash = hash_media_event(session)

        view = self.HistoryOptions()

        media_table = self.bot.database.get_table("plex_watched_media")

        media_entry = media_table.get_row(media_guid=session.guid, guild_id=guild.id)
        if not media_entry:  # If no media entry exists with this guid, fallback to the media name
            logging.debug(f"Could not find GUID {session.guid} in database, falling back to media name")
            if session.type == "episode":
                media_entry = media_table.get_row(title=session.grandparentTitle, season_num=session.parentIndex,
                                                  ep_num=session.index, guild_id=guild.id)
            else:
                media_entry = media_table.get_row(title=session.title, media_year=session.year,
                                                  media_type=session.type, guild_id=guild.id)
        if not media_entry:  # If no media entry exists at all, insert a new one
            logging.debug(f"Could not find media entry for {session.title} in database, creating new entry")
            media_table.add(guild_id=guild.id, media_guid=session.guid,
                            title=session.title, media_year=session.year,
                            media_length=round(session.duration / 1000),
                            media_type=session.type, library_id=session.librarySectionID)
            media_entry = media_table.get_row(media_guid=session.guid, guild_id=guild.id)

        if session.type == "episode":
            parent_show = media_table.get_row(title=session.grandparentTitle, guild_id=guild.id,
                                              media_type="show")
            if not parent_show:
                media_table.add(guild_id=guild.id, media_guid=session.grandparentGuid,
                                title=session.grandparentTitle, media_year=session.show().year,
                                media_length=round(get_series_duration(session.show()) / 1000),
                                media_type="show", library_id=session.librarySectionID)
                parent_show = media_table.get_row(title=session.grandparentTitle, guild_id=guild.id,
                                                  media_type="show")
            media_entry.set(season_num=session.parentIndex, ep_num=session.index, show_id=parent_show["media_id"])

        event_table = self.bot.database.get_table("plex_history_events")
        entry = event_table.add(event_id=m_hash, guild_id=guild.id,
                                history_time=datetime.datetime.now().timestamp(),
                                account_id=accountID, media_id=media_entry["media_id"],
                                pb_start_offset=raw_start_position,
                                pb_end_offset=raw_current_position,
                                session_duration=alive_time.seconds * 1000,
                                watch_time=round(watch_time * 1000))

        msg = await channel.send(embed=embed, view=view)

        msg_table = self.bot.database.get_table("plex_history_messages")
        msg_table.add(guild_id=msg.guild.id, message_id=msg.id, event_id=entry["event_id"])

    @has_permissions(manage_messages=True)
    @commands.command(name="manual_history", aliases=["add_event"],
                      description="Manually add a history entry if it was missed")
    async def manual_history(self, ctx, user_id: int, media_id):
        history_channel_id = self.bot.database.get_table(
            "plex_history_channel").get_row(guild_id=ctx.guild.id)["channel_id"]
        history_channel = self.bot.get_channel(history_channel_id)
        media = self.bot.database.get_table("plex_watched_media").get_row(media_id=media_id, guild_id=ctx.guild.id)
        if not media:
            await ctx.send("Could not find media with that ID")
            return
        user = ctx.guild.get_member(user_id)
        if not user:
            plex_user = ctx.plex.associations.get(user_id)
        else:
            plex_user = ctx.plex.associations.get(user)

        media_hash = hash_media_event(media)
        # Assume the user watched the whole thing and that the session was alive for the same amount of time
        event_table = self.bot.database.get_table("plex_history_events")
        message_table = self.bot.database.get_table("plex_history_messages")
        event_table.add(event_id=media_hash, guild_id=ctx.guild.id,
                        history_time=datetime.datetime.now().timestamp(),
                        account_id=plex_user.id(plex_only=True), media_id=media["media_id"],
                        pb_start_offset=0, pb_end_offset=media["media_length"] * 1000,
                        session_duration=media["media_length"] * 1000,
                        watch_time=media["media_length"] * 1000)

        length = datetime.timedelta(seconds=media["media_length"])
        text = f"{plex_user.mention()} watched this with `Unknown` on `Unknown`\n" \
               f"They watched `{length}` of `{length}`"
        embed = discord.Embed(description=text, color=discord.Color.yellow())
        if media["media_type"] == "episode":
            show = self.bot.database.get_table("plex_watched_media").get_row(
                media_id=media["show_id"], guild_id=ctx.guild.id)
            embed.set_author(name=f"{show['title']} - S{media['season_num']}E{media['ep_num']}",
                             icon_url=plex_user.avatar_url())
            embed.title = f"{media['title']}"
        else:
            embed.set_author(name=f"{media['title']} ({media['media_year']})",
                             icon_url=plex_user.avatar_url())
        start_position = datetime.timedelta(seconds=0)
        current_position = datetime.timedelta(seconds=media["media_length"])
        progress_bar = text_progress_bar_maker(media["media_length"], 0, media["media_length"])
        embed.add_field(name=f"Progress: {start_position}->{current_position}",
                        value=progress_bar, inline=False)

        # Add the components
        view = self.HistoryOptions()
        embed.set_footer(text="This session was added manually")
        msg = await history_channel.send(embed=embed, view=view)
        message_table.add(guild_id=ctx.guild.id, message_id=msg.id, event_id=media_hash)
        await ctx.send("Added history entry")

    @has_permissions(administrator=True)
    @command(name="set_history_channel", aliases=["shc"])
    async def set_history_channel(self, ctx, channel: discord.TextChannel):
        cursor = self.bot.database.execute(
            '''INSERT OR REPLACE INTO plex_history_channel VALUES (?, ?)''', (ctx.guild.id, channel.id))
        self.bot.database.commit()
        await ctx.send(f"Set history channel to {channel.mention}")

    @has_permissions(administrator=True)
    @command(name="update_components", aliases=["uc"])
    async def update_components(self, ctx):
        """
        Updates the components on history messages to the new HistoryOptions view
        """
        table = self.bot.database.get_table("plex_history_messages")
        channel = self.bot.database.get_table("plex_history_channel").get_row(guild_id=ctx.guild.id)["channel_id"]
        message_cache = {}
        await ctx.send(f"Fetching messages from {ctx.guild.get_channel(channel).mention}")
        async for message in ctx.guild.get_channel(channel).history(limit=None):
            if message.author == self.bot.user:
                message_cache[message.id] = message
        logging.info(f"Updating {len(message_cache)} messages")
        estimated_time = len(message_cache) * 7.5 / 60  # 0.5 seconds per message
        await ctx.send(f"Updating {len(message_cache)} messages, "
                       f"this will take about {round(estimated_time, 2)} minutes")
        for entry in table.get_all(reverse=True):
            if entry["message_id"] in message_cache:
                message = message_cache[entry["message_id"]]
                # Check if the message's buttons have the right custom_id
                if len(message.components) > 0:
                    if message.components[0].children[2].custom_id == "addrating":
                        continue
                view = self.HistoryOptions()
                await message.edit(view=view)
                await asyncio.sleep(7.5)
        await ctx.send("All messages updated to new component format")

    @has_permissions(administrator=True)
    @command(name="clean_history", aliases=["ch"])
    async def clean_history(self, ctx):
        """Check for any unmatched history messages and remove them from the database"""
        table = self.bot.database.get_table("plex_history_messages")
        channel = self.bot.database.get_table("plex_history_channel").get_row(guild_id=ctx.guild.id)["channel_id"]
        message_cache = {}
        await ctx.send(f"Fetching messages from {ctx.guild.get_channel(channel).mention}")
        async for message in ctx.guild.get_channel(channel).history(limit=None):
            if message.author == self.bot.user:
                message_cache[message.id] = message
        logging.info(f"Checking {len(message_cache)} messages")
        # Check if any messages are in the database but not in the channel
        removed = 0
        for entry in table.get_all():
            if entry["message_id"] not in message_cache:
                logging.info(f"Removing {entry['message_id']} from database")
                table.delete(message_id=entry["message_id"])
                removed += 1
        await ctx.send(f"Removed {removed} unmatched watch logs from the database")

    @has_permissions(administrator=True)
    @command(name="migrate_history", aliases=["mh"])
    async def migrate_history(self, ctx):
        media_table = self.bot.database.get_table("plex_watched_media")
        updated, failed = 0, 0
        embed = discord.Embed(title="Updating Metadata for Watched Media",
                              description="Progress: ?/?", color=discord.Color.yellow())
        msg = await ctx.send(embed=embed)
        rows = media_table.get_rows(guild_id=ctx.guild.id)
        row_count = len(rows)

        # Add the data to the content GUID and library ID columns
        for media in rows:
            # Get the media object from plex
            if media["media_type"] == "episode":
                show = media_table.get_row(title=media["title"], media_type="show")
                if not show:
                    show = media_table.get_row(media_id=media["show_id"])

                if not show:
                    content = get_episode(ctx.plex, media["title"],
                                          season=media["season_num"], episode=media["ep_num"])
                    if not content:
                        continue
                    media_table.add(title=content.grandparentTitle, media_type="show", guild_id=ctx.guild.id,
                                    media_guid=content.grandparentGuid, media_year=content.show().year,
                                    library_id=content.librarySectionID,
                                    media_length=round(get_series_duration(content.show()) / 1000))
                    show = media_table.get_row(title=content.grandparentTitle, media_type="show",
                                               guild_id=ctx.guild.id)
                    logging.info(f"Added show {content.grandparentTitle} to watched media")
                else:
                    content = get_episode(ctx.plex, show["title"],
                                          season=media["season_num"], episode=media["ep_num"])

                if content is None or show is None:
                    logging.warning(f"Failed to find episode"
                                    f" {media['title']} S{media['season_num']}E{media['ep_num']}")
                    failed += 1
                    continue

                media.set(title=content.title, media_guid=content.guid, library_id=content.librarySectionID,
                          media_length=round(content.duration / 1000), show_id=show["media_id"])
                show.set(media_length=round(get_series_duration(content.show()) / 1000))

            else:
                results = ctx.plex.search(media["title"], mediatype=media["media_type"])
                possible_contents = [r for r in results if isinstance(r, plexapi.video.Video)]
                # Find the content with the same media year and media type
                content = None
                for possible_content in possible_contents:
                    if possible_content.year == media["media_year"] and possible_content.type == media["media_type"]:
                        content = possible_content
                        break
                else:
                    if len(possible_contents) == 1:
                        content = possible_contents[0]
                        # logging.warning(f"Found {content.title} ({content.year}) instead of {media['title']} "
                        #                 f"({media['media_year']})")
                    else:
                        logging.warning(f"Failed to find {media['title']} ({media['media_year']}) search returned "
                                        f"{len(possible_contents)} results but none matched")

                if content is None:
                    failed += 1
                    continue

                media.set(media_guid=content.guid, library_id=content.librarySectionID,
                          media_length=round(content.duration / 1000))
                # logging.debug(f"Updated [{media['title']}] with content GUID {content.guid}"
                #               f" and library ID {content.librarySectionID}")
            if updated % 10 == 0:
                embed.description = f"Progress: {updated}/{row_count} [Failed: {failed}]"
                await msg.edit(embed=embed)
            updated += 1

        # Update the embed one last time
        embed.description = f"Progress: {updated}/{row_count}"
        embed.colour = discord.Color.green()
        await msg.edit(embed=embed)
        logging.info(f"Updated {updated} rows with content GUID, library ID and media length")


async def setup(bot):
    bot.add_view(PlexHistory.HistoryOptions())
    await bot.add_cog(PlexHistory(bot))
    logging.info("PlexHistory loaded successfully")
