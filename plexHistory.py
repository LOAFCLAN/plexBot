import asyncio
import datetime
import random

import discord
import plexapi.video
from discord.ext import commands
from discord.ext.commands import Cog, command, has_permissions
import custom_dpy_overrides
from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction, ActionRow

from plex_wrappers import SessionChangeWatcher, SessionWatcher
from utils import base_info_layer, get_season, get_episode, cleanup_url


def hash_media_event(media) -> int:
    """Hash a media watch event, so we can easily reference it later
    The hash is based on the medias title, guid, userID of the watcher and the viewedAt
    """
    if media.type == "episode":
        values = (
        int(hash(media.usernames[0])), int(media.lastViewedAt.timestamp()), int(media.parentIndex), int(media.index))
    else:
        values = random.randint(0, 1000000)
    val_hash = hash(values)
    return val_hash


class PlexHistory(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.msg_cache = {}
        self.cached_history = {}
        self.sent_hashes = []

    @Cog.listener('on_ready')
    async def on_ready(self):

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

        print(f"Started {self.__class__.__name__}")

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
            if event[2] not in self.msg_cache[guild.id]:
                self.msg_cache[guild.id][event[2]] = await channel.fetch_message(event[2])
                await self.acquire_history_message(guild, channel, self.msg_cache[guild.id][event[2]])
            else:
                await self.acquire_history_message(guild, channel, self.msg_cache[guild.id][event[2]])
            self.sent_hashes.append(event[0])
        print(f"Acquired {len(self.msg_cache[guild.id])} messages for {guild.name}")

        # while True:
        #     history = plex.history(maxresults=100)
        #     # Filter any media that is missing viewedAt and/or accountID
        #     history = [m for m in history if m.viewedAt is not None and m.accountID is not None]
        #     # Sort the history by viewedAt
        #     history = sorted(history, key=lambda x: x.viewedAt)
        #     # Filter an
        #     for event in history:
        #
        #         m_hash = hash_media_event(event)
        #
        #         if m_hash not in self.sent_hashes:
        #             if isinstance(event, plexapi.video.Episode):
        #                 title = event.grandparentTitle
        #             else:
        #                 title = event.title
        #             self.bot.database.execute(
        #                 '''INSERT INTO plex_history_messages
        #                 (event_hash, guild_id, message_id, history_time, title, media_type, account_ID)
        #                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
        #                 (m_hash, guild.id, None, event.viewedAt, title, event.type, event.accountID))
        #             await self.send_history_message(guild, channel, event, plex)
        #
        #             if isinstance(event, plexapi.video.Episode):
        #                 self.bot.database.execute('''
        #                 UPDATE plex_history_messages SET season_num = ?, ep_num = ? WHERE event_hash = ?''',
        #                                           (event.parentIndex, event.index, m_hash))
        #
        #             self.bot.database.commit()
        #             self.sent_hashes.append(m_hash)
        #
        #     await asyncio.sleep(30)

    async def on_watched(self, watcher, channel):
        session = watcher.initial_session
        if session.type == "episode":
            title = session.grandparentTitle
        else:
            title = session.title
        m_hash = hash_media_event(watcher.session)
        if m_hash not in self.sent_hashes:
            self.bot.database.execute(
                '''INSERT INTO plex_history_messages
                (event_hash, guild_id, message_id, history_time, title, media_type, account_ID)
                VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (m_hash, channel.guild.id, None, session.viewedAt, title, session.type, session.accountID))
            await self.send_history_message(channel.guild, channel, watcher, await self.bot.fetch_plex(channel.guild))
            if isinstance(session, plexapi.video.Episode):
                self.bot.database.execute('''
                UPDATE plex_history_messages SET season_num = ?, ep_num = ? WHERE event_hash = ?''',
                                          (session.parentIndex, session.index, m_hash))
            self.bot.database.commit()
            self.sent_hashes.append(m_hash)

    async def acquire_history_message(self, guild, channel, msg):
        if hasattr(msg, "components"):
            for component in msg.components:
                if isinstance(component, ActionRow):
                    for thing in component.components:
                        if isinstance(thing, Button):
                            self.bot.component_manager.add_callback(thing, self.component_callback)
                            # print(f"Reattached component callback to {msg.id}")
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

        session = watcher.session
        media = watcher.media
        accountID = session.accountID
        device = None
        if accountID is None:
            username = session.usernames[0]
            for user in plex.systemAccounts():
                if user.name == username:
                    accountID = user.accountID
                    break

        if len(session.players) >= 1:
            device_name = session.players[0].machineIdentifier
            device = None
            for sys_device in plex.systemDevices():
                if sys_device.clientIdentifier == device_name:
                    device = sys_device
                    break
        else:
            device = None
            accountID = None

        user = plex.associations.get_discord_association(accountID)
        if user is None:
            user = plex.systemAccount(accountID)

        time = watcher.alive_time

        progress = pl

        if isinstance(user, discord.User):
            embed = discord.Embed(title=session.title,
                                  description=
                                  f"{user.mention} watched this with `{device.name}` on `{device.platform}`",
                                  color=0x00ff00, timestamp=time)
            if session.type == "episode":
                embed.set_author(name=f"{session.grandparentTitle} - S{session.parentIndex}E{session.index}",
                                 icon_url=user.avatar_url)
        else:
            embed = discord.Embed(title=session.title,
                                  description=f"`{user.name}` "
                                              f"watched this with `{device.name}` on `{device.platform}`",
                                  color=0x00ff00, timestamp=time)
            if session.type == "episode":
                embed.set_author(name=f"{session.grandparentTitle} - S{session.parentIndex}E{session.index}",
                                 icon_url=user.thumb)
            elif session.type == "movie":
                embed.set_author(name="", icon_url=user.thumb)

        # if

        if hasattr(session, "thumb"):
            thumb_url = cleanup_url(session.thumbUrl)
            embed.set_thumbnail(url=thumb_url)

        m_hash = hash_media_event(session)

        # Generate more info components
        button = Button(
            label="More Info",
            style=ButtonStyle.blue,
            id=f"historymore_{m_hash}",
        )
        self.bot.component_manager.add_callback(button, self.component_callback)
        msg = await channel.send(embed=embed, components=[button])
        if session.type == "episode":
            title = session.grandparentTitle
        else:
            title = session.title
        self.bot.database.execute(
                            '''INSERT INTO plex_history_messages
                            (event_hash, guild_id, message_id, history_time,
                             title, media_type, account_ID, playback_progress)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                            (m_hash, guild.id, msg.id, time.timestamp(), title, session.type, accountID, 0))
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
        if interaction.custom_id.startswith("historymore"):
            m_hash = int(interaction.custom_id.split("_")[1])
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

            await interaction.respond(embed=embed)

    @has_permissions(administrator=True)
    @command(name="set_history_channel", aliases=["shc"])
    async def set_history_channel(self, ctx, channel: discord.TextChannel):
        cursor = self.bot.database.execute(
            '''INSERT OR REPLACE INTO plex_history_channel VALUES (?, ?)''', (ctx.guild.id, channel.id))
        self.bot.database.commit()
        await ctx.send(f"Set history channel to {channel.mention}")


def setup(bot):
    bot.add_cog(PlexHistory(bot))
    print(f"Loaded {__name__}")
