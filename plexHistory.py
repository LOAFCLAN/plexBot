import asyncio
import datetime
import random

import discord
import plexapi.video
from discord import Interaction, ButtonStyle, ActionRow
from discord.ext import commands
from discord.ext.commands import Cog, command, has_permissions
# import custom_dpy_overrides
# from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction, ActionRow
from discord.ui import Button, View, Select

from plex_wrappers import SessionChangeWatcher, SessionWatcher
from utils import base_info_layer, get_season, get_episode, cleanup_url, text_progress_bar_maker, stringify, \
    base_user_layer

from loguru import logger as logging


def hash_media_event(media) -> int:
    """Hash a media watch event, so we can easily reference it later
    The hash is based on the medias title, guid, userID of the watcher and the viewedAt
    """
    return hash(hash(media) + hash(datetime.datetime.now()))


class PlexHistory(commands.Cog):
    class HistoryOptions(discord.ui.View):

        def __init__(self, *, timeout=None):
            super().__init__(timeout=timeout)

        @discord.ui.button(label="Media Info", style=ButtonStyle.blurple, custom_id="mediainfo",
                           emoji="📹")
        async def media_info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Get the message ID
            message_id = interaction.message.id
            # Get the event from the database
            table = interaction.client.database.get_table("plex_history_messages")
            event = table.get_row(message_id=message_id)
            # Get the media object
            media = await self.media_from_message(interaction.guild, interaction.client, event)
            # Get the embed
            embed = self.media_embed(media)
            # Send the embed
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @discord.ui.button(label="User Info", style=ButtonStyle.green, custom_id="userinfo",
                           emoji="\N{BUSTS IN SILHOUETTE}")
        async def user_info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Get the message ID
            message_id = interaction.message.id
            # Get the event from the database
            table = interaction.client.database.get_table("plex_history_messages")
            event = table.get_row(message_id=message_id)
            # Get the user ID
            account_id = event["account_ID"]
            guild = interaction.guild
            plex = await interaction.client.fetch_plex(guild)
            user = plex.associations.get(account_id)
            embed = base_user_layer(user, interaction.client.database)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @discord.ui.button(label="Mobile View", style=ButtonStyle.green, custom_id="mobileview",
                           emoji="📱")
        async def mobile_view_button(self, button: discord.ui.Button, interaction: Interaction):
            pass

        @staticmethod
        async def media_from_message(guild, client, entry):
            plex = await client.fetch_plex(guild)
            if entry[5] == "episode":
                media = get_episode(plex, entry[4], season=entry[6], episode=entry[7])
                return media
            else:
                name = entry[4]
                media = plex.search(name)[0]
                return media

        @staticmethod
        def media_embed(content):

            if content.isPartialObject():  # If the media is only partially loaded
                content.reload()  # do it correctly this time

            if isinstance(content, plexapi.video.Movie):
                embed = discord.Embed(title=f"{content.title} ({content.year})",
                                      description=f"{content.tagline}", color=0x00ff00)
                base_info_layer(embed, content)  # Add the base info layer to the embed

            elif isinstance(content, plexapi.video.Episode):  # ------------------------------------------------------
                """Format the embed being sent for an episode"""
                embed = discord.Embed(title=f"{content.grandparentTitle}\n{content.title} "
                                            f"(S{content.parentIndex}E{content.index})",
                                      description=f"{content.summary}", color=0x00ff00)
                base_info_layer(embed, content)

            else:
                embed = discord.Embed(title=f"Unknown media type", color=0x00ff00)

            if hasattr(content, "thumb"):
                thumb_url = cleanup_url(content.thumb)
                embed.set_thumbnail(url=thumb_url)

            return embed

    def __init__(self, bot):
        self.bot = bot
        self.msg_cache = {}
        self.cached_history = {}
        self.sent_hashes = []
        self.button_cache = []

    @Cog.listener('on_ready')
    async def on_ready(self):
        logging.info("Starting PlexHistory Cog")
        cursor = self.bot.database.execute(
            '''SELECT * FROM plex_history_messages''')
        for row in cursor.fetchall():
            guild_id = row[1]
            if guild_id not in self.cached_history:
                self.cached_history[guild_id] = {}
            self.cached_history[guild_id][row[0]] = {"message_id": row[2],
                                                     "history_time": row[4]}

        cursor = self.bot.database.execute(
            '''SELECT * FROM plex_history_channel''')
        for row in cursor:
            self.msg_cache[row[0]] = {}
            asyncio.get_event_loop().create_task(self.history_watcher(row[0], row[1]))

        logging.info("PlexHistory startup complete")

    async def history_watcher(self, guild_id, channel_id):
        channel = await self.bot.fetch_channel(channel_id)
        guild = await self.bot.fetch_guild(guild_id)
        plex = await self.bot.fetch_plex(guild)

        SessionChangeWatcher(plex, self.on_watched, channel)

    async def on_watched(self, watcher, channel):
        m_hash = hash_media_event(watcher.session)
        if m_hash not in self.sent_hashes:
            await self.send_history_message(channel.guild, channel, watcher, await self.bot.fetch_plex(channel.guild))

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

        time = watcher.alive_time

        raw_current_position = watcher.end_offset
        raw_duration = session.duration
        raw_start_position = start_session.viewOffset

        progress_bar = text_progress_bar_maker(raw_duration, raw_current_position, raw_start_position)

        current_position = datetime.timedelta(seconds=round(raw_current_position / 1000))
        duration = datetime.timedelta(seconds=round(raw_duration / 1000))
        start_position = datetime.timedelta(seconds=round(raw_start_position / 1000))

        # if isinstance(user, discord.User):
        embed = discord.Embed(description=
                              f"{user.mention()} "
                              f"watched this with `{device.name}` on `{device.platform.capitalize()}`",
                              color=0x00ff00, timestamp=time)
        if session.type == "episode":
            embed.title = f"{session.title} {f'({session.year})' if session.type != 'episode' else ''}"
            embed.set_author(name=f"{session.grandparentTitle} - "
                                  f"S{str(session.parentIndex).zfill(2)}E{str(session.index).zfill(2)}",
                             icon_url=user.avatar_url())
        else:
            embed.set_author(name=f"{session.title} ({session.year})", icon_url=user.avatar_url())
        # else:
        #     embed = discord.Embed(title=f"{session.title} {f'({session.year})' if session.type != 'episode' else ''}",
        #                           description=
        #                           f"`{user.name}` "
        #                           f"watched this with `{device.name}` on `{device.platform.capitalize()}`",
        #                           color=0x00ff00, timestamp=time)
        #     if session.type == "episode":
        #         embed.set_author(name=f"{session.grandparentTitle} - S{session.parentIndex}E{session.index}",
        #                          icon_url=user.thumb)
        #     elif session.type == "movie":
        #         embed.set_author(name="", icon_url=user.thumb)

        embed.add_field(name=f"Progress: ({start_position}->{current_position}) {duration}",
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

        msg = await channel.send(embed=embed, view=view)

        if session.type == "episode":
            title = session.grandparentTitle
        else:
            title = session.title

        table = self.bot.database.get_table("plex_history_messages")
        entry = table.add(event_hash=m_hash, guild_id=guild.id, message_id=msg.id,
                          history_time=datetime.datetime.now().timestamp(),
                          title=title, media_type=session.type, account_ID=accountID,
                          pb_start_offset=raw_start_position,
                          pb_end_offset=raw_current_position, media_year=session.year,
                          session_duration=alive_time.seconds * 1000,
                          watch_time=round(watch_time * 1000))
        if isinstance(session, plexapi.video.Episode):
            entry.set(season_num=session.parentIndex, ep_num=session.index)

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
        async for message in ctx.guild.get_channel(channel).history(limit=None):
            if message.author == self.bot.user:
                message_cache[message.id] = message
        logging.info(f"Updating {len(message_cache)} messages")
        estimated_time = len(message_cache) * 5 / 60  # 0.5 seconds per message
        await ctx.send(f"Updating {len(message_cache)} messages, "
                       f"this will take about {round(estimated_time, 2)} minutes")
        for entry in table.get_all():
            if entry["message_id"] in message_cache:
                message = message_cache[entry["message_id"]]
                # Check if the message's buttons have the right custom_id
                if message.components[0].children[0].custom_id == "mediainfo":
                    continue
                view = self.HistoryOptions()
                await message.edit(view=view)
                await asyncio.sleep(5)


async def setup(bot):
    bot.add_view(PlexHistory.HistoryOptions())
    await bot.add_cog(PlexHistory(bot))
    logging.info("PlexHistory loaded successfully")
