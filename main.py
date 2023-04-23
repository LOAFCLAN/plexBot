import datetime
import os
import re
import sys
import traceback
from decimal import InvalidContext

import sqlite3

from discord.ext import commands
from discord.utils import oauth_url
from plexapi.server import PlexServer
import discord

import utils
from plex_wrappers import plex_servers, PlexContext, discord_associations, DiscordAssociations
from discord_components import DiscordComponents, Button, ButtonStyle

activity = PlexServer.activities


class PlexBot(commands.Bot):

    async def restart(self):
        await self.close()
        # self.loop.stop()

    async def shutdown(self):
        """Shuts down the bot"""
        await self.close()
        os.popen("systemctl stop plexbot.service").read()
        self.loop.stop()

    def database_init(self):
        self.database.execute(
            '''CREATE TABLE IF NOT EXISTS plex_servers (guild_id INTEGER PRIMARY KEY, server_url TEXT, 
            server_token TEXT);''')
        self.database.execute(
            '''CREATE TABLE IF NOT EXISTS discord_associations (guild_id INTEGER,
             discord_user_id INTEGER, plex_id INTEGER, plex_email 
            TEXT, plex_username TEXT, PRIMARY KEY (guild_id, discord_user_id));''')
        self.database.execute(
            '''CREATE TABLE IF NOT EXISTS activity_messages (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, 
            message_id INTEGER);''')
        self.database.execute(
            '''CREATE TABLE IF NOT EXISTS plex_alert_channel (guild_id INTEGER PRIMARY KEY, channel_id INTEGER);''')
        self.database.execute(
            '''CREATE TABLE IF NOT EXISTS plex_history_channel(guild_id INTEGER PRIMARY KEY, channel_id INTEGER);''')
        self.database.execute(
            '''CREATE TABLE IF NOT EXISTS plex_history_messages 
            (event_hash INTEGER, guild_id INTEGER, message_id INTEGER, history_time FLOAT (0.0), 
            title TEXT NOT NULL, media_type TEXT NOT NULL, season_num INTEGER, ep_num INTEGER, account_ID INTEGER,
            pb_start_offset FLOAT (0.0, 1.0), pb_end_offset FLOAT (0.0, 1.0), media_year TEXT, session_duration FLOAT (0.0, 1.0),
            PRIMARY KEY (event_hash, guild_id));''')

        self.database.execute(
            '''CREATE TABLE IF NOT EXISTS plex_devices
            (account_id INTEGER, device_id STRING, last_seen INT, PRIMARY KEY (account_id, device_id));''')

        # Check if the plex_history_messages table a session_duration column, if not add it and set its value to the
        # difference between the end and start offset
        cursor = self.database.execute('''PRAGMA table_info(plex_history_messages)''')
        columns = cursor.fetchall()
        if len(columns) == 12:
            self.database.execute('''ALTER TABLE plex_history_messages ADD COLUMN session_duration FLOAT (0.0, 1.0)''')
            self.database.execute('''UPDATE plex_history_messages SET session_duration = pb_end_offset - pb_start_offset''')
            self.database.commit()

        self.database.commit()

    def __init__(self, *args, **kwargs):
        self.component_manager = None
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
        self.load_extension('plexSearch')
        self.load_extension('plexHistory')
        self.client = super()

    def owner(self):
        return super().owner_id

    async def get_context(self, message, *, cls=PlexContext):
        ctx = await super().get_context(message, cls=cls)
        return ctx

    async def fetch_plex(self, guild: discord.Guild) -> PlexServer:
        """Allows for getting a plex instance for a guild if ctx is not available"""
        guild_id = guild.id
        if guild_id not in plex_servers:
            cursor = self.database.execute("SELECT * FROM plex_servers WHERE guild_id = ?", (guild_id,))
            if cursor.rowcount == 0:
                raise Exception("No plex server found for this guild")
            row = cursor.fetchone()
            try:
                plex_servers[guild_id] = PlexServer(row[1], row[2])
                plex_servers[guild_id].baseurl = row[1]
                plex_servers[guild_id].token = row[2]
            except Exception:
                raise Exception("Invalid plex server credentials, or server is offline")
        if not hasattr(plex_servers[guild_id], "associations"):
            discord_associations.update({guild_id: DiscordAssociations(self, guild)})
            plex_servers[guild_id].associations = discord_associations[guild_id]
        if not hasattr(plex_servers[guild_id], "database"):
            plex_servers[guild_id].database = self.database
        return plex_servers[guild_id]

    async def on_ready(self):
        for guild in self.guilds:
            await guild.chunk(cache=True)
        print(f'Logged in as {self.user.name}')
        print(f'Bot ID: {self.user.id}')
        await self.change_presence(activity=discord.Game(name="PlexBot"))
        # To get the activity message IDs and channel IDs
        # Print bot invite link to console
        perms = 469830672
        print('<{}>'.format(oauth_url(super().user.id, discord.Permissions(perms))))
        print("Loading all members")
        self.component_manager = DiscordComponents(self)

    def run(self):
        super().run(self.token)

    async def on_command_error(self, context, exception):
        if isinstance(exception, commands.NoPrivateMessage):
            await context.send('{}, This command cannot be used in DMs.'.format(context.author.mention))
        elif isinstance(exception, commands.UserInputError):
            pass  # Silent ignore
            await context.send('{}, {}'.format(context.author.mention, self.format_error(context, exception)))
        elif isinstance(exception, commands.NotOwner):
            await context.send('{}, {}'.format(context.author.mention, exception.args[0]))
        elif isinstance(exception, commands.MissingPermissions):
            permission_names = [name.replace('guild', 'server').replace('_', ' ').title() for name in
                                exception.missing_perms]
            await context.send('{}, you need {} permissions to run this command!'.format(
                context.author.mention, utils.pretty_concat(permission_names)))
        elif isinstance(exception, commands.BotMissingPermissions):
            permission_names = [name.replace('guild', 'server').replace('_', ' ').title() for name in
                                exception.missing_perms]
            await context.send('{}, I need {} permissions to run this command!'.format(
                context.author.mention, utils.pretty_concat(permission_names)))
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
        elif isinstance(exception, commands.CheckFailure):
            await context.send('{}, {}'.format(context.author.mention, exception.args[0]))
        else:
            await context.send(
                '```\n%s\n```' % ''.join(traceback.format_exception_only(type(exception), exception)).strip())
            # Print traceback to console
            print(''.join(traceback.format_exception(type(exception), exception, exception.__traceback__)).strip())
            if isinstance(context.channel, discord.TextChannel):
                pass  # Silent ignore
            else:
                pass

    @staticmethod
    def format_error(ctx, err, *, word_re=re.compile('[A-Z][a-z]+')):
        """Turns an exception into a user-friendly (or -friendlier, at least) error message."""
        type_words = word_re.findall(type(err).__name__)
        type_msg = ' '.join(map(str.lower, type_words))

        if err.args:
            return '%s: %s' % (type_msg, utils.clean(ctx, err.args[0]))
        else:
            return type_msg


intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.messages = True
try:
    plex_bot = PlexBot(intents=intents)
    plex_bot.run()
except discord.errors.PrivilegedIntentsRequired:
    print("Bot requires privileged intents, starting in safe mode.")
    plex_bot = PlexBot(intents=discord.Intents.default())
    plex_bot.run()
except Exception as e:
    print(e)
    print(''.join(traceback.format_exception(type(e), e, e.__traceback__)).strip())
    print("Bot failed to start, exiting.")
    exit(1)
