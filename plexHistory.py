import datetime

import discord
import plexapi.video
from discord.ext import commands
from discord.ext.commands import Cog, command, has_permissions
from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction
from utils import base_info_layer, get_season


def hash_media_event(media) -> int:
    """Hash a media watch event, so we can easily reference it later
    The hash is based on the medias title, guid, userID of the watcher and the viewedAt
    """
    values = (int(media.accountID), int(media.viewedAt.timestamp()))
    val_hash = hash(values)
    return val_hash


class PlexHistory(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.msg_cache = {}
        self.cached_history = {}

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
            channel = await self.bot.fetch_channel(row[1])
            async for msg in channel.history(limit=None):
                if msg.author.id == self.bot.user.id:
                    self.msg_cache[row[0]][msg.id] = msg
            self.bot.loop.create_task(self.history_watcher(row[0], row[1]))
        print(f"Started {self.__class__.__name__}")

    async def history_watcher(self, guild_id, channel_id):
        channel = await self.bot.fetch_channel(channel_id)
        guild = await self.bot.fetch_guild(guild_id)
        plex = await self.bot.fetch_plex(guild)

        while True:
            history = plex.history(maxresults=100)

            # Filter any media that is missing viewedAt and/or accountID
            history = [m for m in history if m.viewedAt is not None and m.accountID is not None]

            # Sort the history by viewedAt
            history = sorted(history, key=lambda x: x.viewedAt)
            # Filter an
            for event in history:

                m_hash = hash_media_event(event)
                if m_hash not in self.cached_history[guild_id]:
                    self.cached_history[guild.id][m_hash] = {"message_id": None,
                                                             "history_time": event.viewedAt}
                    if isinstance(event, plexapi.video.Episode):
                        title = event.grandparentTitle
                    else:
                        title = event.title
                    self.bot.database.execute(
                        '''INSERT INTO plex_history_messages 
                        (event_hash, guild_id, message_id, history_time, title, media_type, account_ID) 
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        (m_hash, guild.id, None, event.viewedAt, title, event.type, event.accountID))
                    await self.send_history_message(guild, channel, event, plex)

                    if isinstance(event, plexapi.video.Episode):
                        self.bot.database.execute('''
                        UPDATE plex_history_messages SET season_num = ?, ep_num = ? WHERE event_hash = ?''',
                                                  (event.parentIndex, event.index, m_hash))

                    self.bot.database.commit()
                else:
                    cursor = self.bot.database.execute(
                        '''SELECT * FROM plex_history_messages WHERE event_hash = ?''', (m_hash,))
                    value = cursor.fetchone()
                    if value is not None:
                        if value[2] in self.msg_cache[guild.id]:
                            msg = self.msg_cache[guild.id][value[2]]
                            await self.acquire_history_message(guild, channel, msg)

    async def acquire_history_message(self, guild, channel, msg):
        print(f"Acquiring history message {msg.id}")
        if hasattr(msg, "components"):
            for component in msg.components:
                if isinstance(component, Button):
                    self.bot.component_manager.add_callback(component, self.component_callback)
        else:
            # msg = await msg.channel.fetch_message(msg.id)
            # if hasattr(msg, "components"):
            #     for component in msg.components:
            #         if isinstance(component, Button):
            #             self.bot.component_manager.add_callback(component, self.component_callback)
            # else:
            #     print(f"Message {msg.id} has no components")
            pass

    async def send_history_message(self, guild, channel, media, plex):
        embed = discord.Embed(title=media.title, color=0x00ff00)

        user = plex.associations.get_discord_association(media.accountID)
        if user is not None:
            embed.set_author(name=user.display_name, icon_url=user.avatar_url)
        else:
            embed.set_author(name=plex.systemAccount(media.accountID).name)
        embed.set_footer(text=f"{media.viewedAt}")

        m_hash = hash_media_event(media)

        # Generate more info components
        button = Button(
            label="More Info",
            style=ButtonStyle.blue,
            id=f"historymore_{m_hash}",
        )
        self.bot.component_manager.add_callback(button, self.component_callback)
        msg = await channel.send(embed=embed, components=[button])
        cursor = self.bot.database.execute(
            '''UPDATE plex_history_messages SET message_id = ? WHERE event_hash = ?''',
            (msg.id, m_hash))
        self.bot.database.commit()

    async def media_from_hash(self, guild, m_hash):
        plex = await self.bot.fetch_plex(guild)
        cursor = self.bot.database.execute(
            '''SELECT * FROM plex_history_messages WHERE event_hash = ?''', (m_hash,))
        if cursor.rowcount == 0:
            return None
        row = cursor.fetchone()
        if row[5] == "episode":
            name = get_season(plex, row[4], row[6]).episodes()[row[7]]
            media = plex.search(name)[0]
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
