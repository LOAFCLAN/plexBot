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

    def __init__(self, bot):
        self.bot = bot
        self.msg_cache = {}
        self.cached_history = {}
        self.sent_hashes = []

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

        async for msg in channel.history(limit=None):
            if hasattr(msg, "components"):
                if msg.author.id == self.bot.user.id:
                    self.msg_cache[guild.id][msg.id] = msg
            else:
                print(f"Message {msg.id} has no components")
                msg = await msg.channel.fetch_message(msg.id)

        # Re attach component watchers to messages on startup
        events = self.bot.database.execute(
            '''SELECT * FROM plex_history_messages WHERE guild_id = ?''', (guild.id,))
        for event in events.fetchall():
            if event[2] is not None:
                if event[2] not in self.msg_cache[guild.id]:
                    try:
                        self.msg_cache[guild.id][event[2]] = await channel.fetch_message(event[2])
                    except discord.NotFound:
                        print(f"Message {event[2]} not found, removing from database")
                        self.bot.database.execute('''
                        DELETE FROM plex_history_messages WHERE message_id = ?''', (event[2],))
                        self.bot.database.commit()
                    else:
                        await self.acquire_history_message(guild, channel, self.msg_cache[guild.id][event[2]])
                else:
                    await self.acquire_history_message(guild, channel, self.msg_cache[guild.id][event[2]])
            else:
                print(f"Message {event[0]} has no message ID, removing from database")
                self.bot.database.execute(
                    '''DELETE FROM plex_history_messages WHERE event_hash = ?''', (event[0],))
                self.bot.database.commit()

            self.sent_hashes.append(event[0])
        print(f"Acquired {len(self.msg_cache[guild.id])} messages for {guild.name}")

    async def on_watched(self, watcher, channel):
        m_hash = hash_media_event(watcher.session)
        if m_hash not in self.sent_hashes:
            await self.send_history_message(channel.guild, channel, watcher, await self.bot.fetch_plex(channel.guild))

    async def acquire_history_message(self, guild, channel, msg):
        if hasattr(msg, "components"):
            # Attach the button callbacks
            for button in msg.components[0].children:
                # Create a buttonui object from the button
                button_ui = Button(style=button.style, label=button.label, custom_id=button.custom_id)
                # Attach the callback to the buttonui object
                button_ui.callback = self.component_callback
        else:
            print(f"Failed to acquire components for {msg.id}, manually fetching")
            msg = await msg.channel.fetch_message(msg.id)
            if hasattr(msg, "components"):
                for component in msg.components:
                    if isinstance(component, Button):
                        self.bot.component_manager.add_callback(component, self.component_callback)
            else:
                print(f"Message {msg.id} has no components")
            pass

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
        embed.set_footer(text=f"This session was alive for {alive_time}, Started")

        if hasattr(session, "thumb"):
            thumb_url = cleanup_url(session.thumb)
            embed.set_thumbnail(url=thumb_url)

        m_hash = hash_media_event(session)

        # Generate more info components
        media_button = Button(
            label="Media Info",
            emoji="📹",
            style=ButtonStyle.blurple,
            custom_id=f"historymore_{m_hash}",
        )
        media_button.callback = self.component_callback
        user_button = Button(
            label="User Info",
            emoji="\N{BUSTS IN SILHOUETTE}",
            style=ButtonStyle.green,
            custom_id=f"usermore_{accountID}",
        )
        user_button.callback = self.component_callback
        mobile_view_button = Button(
            label="Mobile View",
            emoji="📱",
            style=ButtonStyle.green,
            custom_id=f"mobileview_{m_hash}",
        )
        mobile_view_button.callback = self.component_callback
        view = View()
        view.add_item(media_button)
        view.add_item(user_button)
        view.add_item(mobile_view_button)

        msg = await channel.send(embed=embed, view=view)

        if session.type == "episode":
            title = session.grandparentTitle
        else:
            title = session.title
        self.bot.database.execute(
            '''INSERT INTO plex_history_messages
                            (event_hash, guild_id, message_id, history_time,
                             title, media_type, account_ID, pb_start_offset, pb_end_offset, media_year,
                              session_duration)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (m_hash, guild.id, msg.id, datetime.datetime.now().timestamp(), title, session.type, accountID,
             raw_start_position, raw_current_position, session.year, alive_time.seconds * 1000))
        if isinstance(session, plexapi.video.Episode):
            self.bot.database.execute('''
            UPDATE plex_history_messages SET season_num = ?, ep_num = ? WHERE event_hash = ?''',
                                      (session.parentIndex, session.index, m_hash))
        self.bot.database.commit()

    async def media_from_hash(self, guild, m_hash):
        plex = await self.bot.fetch_plex(guild)
        cursor = self.bot.database.execute(
            '''SELECT * FROM plex_history_messages WHERE event_hash = ?''', (m_hash,))
        if cursor.rowcount == 0:
            return None
        row = cursor.fetchone()
        if row[5] == "episode":
            media = get_episode(plex, row[4], season=row[6], episode=row[7])
            return media
        else:
            name = row[4]
            media = plex.search(name)[0]
            return media

    async def component_callback(self, interaction: Interaction):
        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("historymore"):
            await self.media_info_callback(interaction)
        elif custom_id.startswith("usermore"):
            await self.user_info_callback(interaction)
        elif custom_id.startswith("mobileview"):
            await self.mobile_view_callback(interaction)
        else:
            await interaction.respond(content="Unknown interaction")

    async def media_info_callback(self, interaction: Interaction):
        custom_id = interaction.data["custom_id"]
        m_hash = int(custom_id.split("_")[1])
        guild = interaction.guild

        content = await self.media_from_hash(guild, m_hash)

        if content is None:
            await interaction.respond(content="Could not find media", ephemeral=True)
            return

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

        await interaction.respond(embed=embed)

    async def user_info_callback(self, interaction: Interaction):
        """Format the embed that contains user info"""
        custom_id = interaction.data["custom_id"]
        accountID = int(custom_id.split("_")[1])
        guild = interaction.guild
        plex = await self.bot.fetch_plex(guild)
        user = plex.associations.get(accountID)
        await interaction.respond(embed=base_user_layer(user, self.bot.database))

    async def mobile_view_callback(self, interaction: Interaction):
        """Make a smaller embed that is mobile friendly"""

        await interaction.respond(content="Not implemented yet")

    @has_permissions(administrator=True)
    @command(name="set_history_channel", aliases=["shc"])
    async def set_history_channel(self, ctx, channel: discord.TextChannel):
        cursor = self.bot.database.execute(
            '''INSERT OR REPLACE INTO plex_history_channel VALUES (?, ?)''', (ctx.guild.id, channel.id))
        self.bot.database.commit()
        await ctx.send(f"Set history channel to {channel.mention}")


async def setup(bot):
    await bot.add_cog(PlexHistory(bot))
    logging.info("PlexHistory loaded successfully")
