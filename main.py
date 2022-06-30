import datetime
import os
import sys
import traceback
from decimal import InvalidContext

import sqlite3

from discord.ext import commands
from plexapi.server import PlexServer
import discord

activity = PlexServer.activities

plex_servers = {}


class PlexContext(commands.Context):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        guild_id = self.guild.id
        if guild_id not in plex_servers:
            cursor = self.bot.database.execute("SELECT * FROM plex_servers WHERE guild_id = ?", (guild_id,))
            if cursor.rowcount == 0:
                raise Exception("No plex server found for this guild")
            row = cursor.fetchone()
            try:
                plex_servers[guild_id] = PlexServer(row[1], row[2])
            except Exception:
                raise Exception("Invalid plex server credentials, or server is offline")
        self.plex = plex_servers[guild_id]


class PlexBot(commands.Bot):

    async def shutdown(self, restart=False):
        """Shuts down the bot"""
        self._restarting = restart
        await self.close()
        self.loop.stop()

    def database_init(self):
        self.database.execute(
            '''CREATE TABLE IF NOT EXISTS plex_servers (guild_id INTEGER PRIMARY KEY, server_url TEXT, 
            server_token TEXT);''')
        self.database.execute(
            '''CREATE TABLE IF NOT EXISTS discord_associations (discord_id INTEGER PRIMARY KEY, plex_id INTEGER, plex_email 
            TEXT, plex_username TEXT);''')
        self.database.execute(
            '''CREATE TABLE IF NOT EXISTS activity_messages (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message_id 
            INTEGER);''')
        self.database.commit()

    def __init__(self, *args, **kwargs):
        self.database = sqlite3.connect('plex_bot.db')
        self.database_init()
        self.database.execute('''CREATE TABLE IF NOT EXISTS bot_config (token TEXT, prefix TEXT)''')
        self.database.commit()
        # Get the bot's prefix from the database
        cursor = self.database.execute('''SELECT * FROM bot_config''')
        config = cursor.fetchone()
        if config is None:
            cursor = self.database.execute('''INSERT INTO bot_config VALUES (?, ?)''', ('', '!'))
            self.database.commit()
            config = self.database.cursor()
            print("No config found, created one")
            print("Please set the bot's prefix and token in the database")
            token = input("Token: ")
            prefix = input("Prefix: ")
            self.database.execute('''UPDATE bot_config SET token = ?, prefix = ?''', (token, prefix))
            self.database.commit()
        self.token = config[0]
        super().__init__(command_prefix=config[1], *args, **kwargs)
        for extension in self.extensions:
            self.unload_extension(extension)
        self.load_extension('plexBot')
        self.load_extension('maint')

    async def get_context(self, message, *, cls=PlexContext):
        ctx = await super().get_context(message, cls=cls)
        return ctx

    async def fetch_plex(self, guild: discord.Guild):
        """Allows for getting a plex instance for a guild if ctx is not available"""
        guild_id = guild.id
        if guild_id not in plex_servers:
            cursor = self.database.execute("SELECT * FROM plex_servers WHERE guild_id = ?", (guild_id,))
            if cursor.rowcount == 0:
                raise Exception("No plex server found for this guild")
            row = cursor.fetchone()
            try:
                plex_servers[guild_id] = PlexServer(row[1], row[2])
            except Exception:
                raise Exception("Invalid plex server credentials, or server is offline")
        return plex_servers[guild_id]

    async def on_ready(self):
        print(f'Logged in as {self.user.name}')
        print(f'Bot ID: {self.user.id}')
        # To get the activity message IDs and channel IDs

    def run(self):
        super().run(self.token)

    async def on_command_error(self, context, exception):
        if isinstance(exception, commands.NoPrivateMessage):
            await context.send('{}, This command cannot be used in DMs.'.format(context.author.mention))
        elif isinstance(exception, commands.UserInputError):
            pass  # Silent ignore
            # await context.send('{}, {}'.format(context.author.mention, self.format_error(context, exception)))
        elif isinstance(exception, commands.NotOwner):
            await context.send('{}, {}'.format(context.author.mention, exception.args[0]))
        elif isinstance(exception, commands.MissingPermissions):
            permission_names = [name.replace('guild', 'server').replace('_', ' ').title() for name in
                                exception.missing_perms]
            # await context.send('{}, you need {} permissions to run this command!'.format(
            #     context.author.mention, utils.pretty_concat(permission_names)))
        elif isinstance(exception, commands.BotMissingPermissions):
            permission_names = [name.replace('guild', 'server').replace('_', ' ').title() for name in
                                exception.missing_perms]
            # await context.send('{}, I need {} permissions to run this command!'.format(
            #     context.author.mention, utils.pretty_concat(permission_names)))
        elif isinstance(exception, commands.CommandOnCooldown):
            await context.send(
                '{}, That command is on cooldown! Try again in {:.2f}s!'.format(context.author.mention,
                                                                                exception.retry_after))
        elif isinstance(exception, commands.MaxConcurrencyReached):
            types = {discord.ext.commands.BucketType.default: "`Global`",
                     discord.ext.commands.BucketType.guild: "`Guild`",
                     discord.ext.commands.BucketType.channel: "`Channel`",
                     discord.ext.commands.BucketType.category: "`Category`",
                     discord.ext.commands.BucketType.member: "`Member`", discord.ext.commands.BucketType.user: "`User`"}
            await context.send(
                '{}, That command has exceeded the max {} concurrency limit of `{}` instance! Please try again later.'.format(
                    context.author.mention, types[exception.per], exception.number))
        elif isinstance(exception, (commands.CommandNotFound, InvalidContext)):
            pass  # Silent ignore
        else:
            await context.send(
                '```\n%s\n```' % ''.join(traceback.format_exception_only(type(exception), exception)).strip())
            if isinstance(context.channel, discord.TextChannel):
                pass
                # DOZER_LOGGER.error('Error in command <%d> (%d.name!r(%d.id) %d(%d.id) %d(%d.id) %d)',
                #                    context.command, context.guild, context.guild, context.channel, context.channel,
                #                    context.author, context.author, context.message.content)
            else:
                pass
            #     DOZER_LOGGER.error('Error in command <%d> (DM %d(%d.id) %d)', context.command, context.channel.recipient,
            #                        context.channel.recipient, context.message.content)
            # DOZER_LOGGER.error(''.join(traceback.format_exception(type(exception), exception, exception.__traceback__))


intents = discord.Intents.default()
intents.members = True
intents.presences = True

plex_bot = PlexBot(intents=intents)
plex_bot.run()
