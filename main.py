import asyncio
import datetime
import os
import re
import sqlite3
import sys
import traceback
from decimal import InvalidContext

from ConcurrentDatabase.Database import Database

from discord.ext import commands
from discord.utils import oauth_url
from plexapi.server import PlexServer
import discord

import utils
from plex_wrappers import plex_servers, PlexContext, discord_associations, DiscordAssociations
# from discord_components import DiscordComponents, Button, ButtonStyle
from discord import SelectOption, SelectMenu, Interaction, ButtonStyle, Button

activity = PlexServer.activities

from loguru import logger as logging


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
        self.database.create_table("plex_servers", {"guild_id": "INTEGER PRIMARY KEY", "server_url": "TEXT"})
        self.database.create_table("discord_associations", {"guild_id": "INTEGER", "discord_user_id": "INTEGER",
                                                            "plex_id": "INTEGER", "plex_email": "TEXT",
                                                            "plex_username": "TEXT",
                                                            "PRIMARY KEY": "(guild_id, discord_user_id)"})
        self.database.create_table("activity_messages", {"guild_id": "INTEGER PRIMARY KEY", "channel_id": "INTEGER",
                                                         "message_id": "INTEGER"})
        self.database.create_table("plex_alert_channel", {"guild_id": "INTEGER PRIMARY KEY", "channel_id": "INTEGER"})

        self.database.create_table("plex_history_channel", {"guild_id": "INTEGER PRIMARY KEY", "channel_id": "INTEGER"})

        self.database.create_table("plex_history_messages", {"event_hash": "INTEGER", "guild_id": "INTEGER",
                                                             "message_id": "INTEGER", "history_time": "FLOAT (0.0)",
                                                             "title": "TEXT NOT NULL", "media_type": "TEXT NOT NULL",
                                                             "season_num": "INTEGER", "ep_num": "INTEGER",
                                                             "account_ID": "INTEGER",
                                                             "pb_start_offset": "FLOAT (0.0, 1.0)",
                                                             "pb_end_offset": "FLOAT (0.0, 1.0)",
                                                             "media_year": "TEXT",
                                                             "session_duration": "FLOAT (0.0, 1.0)",
                                                             "PRIMARY KEY": "(event_hash, guild_id)"})
        self.database.update_table("plex_history_messages", 1,
                                   ["ALTER TABLE plex_history_messages ADD COLUMN watch_time FLOAT (0.0, 1.0)",
                                    "UPDATE plex_history_messages SET watch_time = session_duration"])
        # Change the primary key to message_id instead of event_hash
        self.database.update_table("plex_history_messages", 2,
                                   ["CREATE TABLE plex_history_messages_temp (event_hash INTEGER, guild_id INTEGER, "
                                    "message_id INTEGER, history_time FLOAT (0.0), title TEXT NOT NULL, "
                                    "media_type TEXT NOT NULL, season_num INTEGER, ep_num INTEGER, "
                                    "account_ID INTEGER, pb_start_offset FLOAT (0.0, 1.0), "
                                    "pb_end_offset FLOAT (0.0, 1.0), media_year TEXT,"
                                    " session_duration FLOAT (0.0, 1.0), "
                                    "watch_time FLOAT (0.0, 1.0), PRIMARY KEY (message_id))",
                                    "INSERT INTO plex_history_messages_temp SELECT * FROM plex_history_messages",
                                    "DROP TABLE plex_history_messages",
                                    "ALTER TABLE plex_history_messages_temp RENAME TO plex_history_messages"])
        self.database.create_table("plex_devices", {"account_id": "INTEGER", "device_id": "STRING",
                                                    "last_seen": "INT", "PRIMARY KEY": "(account_id, device_id)"})

    @staticmethod
    def db_backup_callback(status, remaining, total):
        if remaining == 0 and status == 101:
            logging.info(f"Database backup complete, {total} pages backed up")
        elif remaining == 0 and status != 101:
            logging.error(f"Database backup failed with status {status}")
        else:
            logging.info(f"Database backup in progress. {remaining} pages remaining.")

    def __init__(self, *args, **kwargs):
        self.database = Database("plex_bot.db")
        self.backup_database = sqlite3.connect("plex_bot.db.bak")
        self.database_init()
        self.session_watchers = []
        self.cog_names = [
            'cogs.plexBot', 'maint', 'cogs.plexSearch', 'cogs.plexHistory', 'cogs.plexStatistics'
        ]
        # self.database.execute('''CREATE TABLE IF NOT EXISTS bot_config (token TEXT, prefix TEXT)''')
        self.database.create_table("bot_config", {"token": "TEXT", "prefix": "TEXT"})
        # self.database.commit()
        # Get the bot's prefix from the database
        settings = self.database.get_table("bot_config").get_entry_by_row(0)
        if settings is None:
            print("No config found, created one")
            print("Please set the bot's prefix and token in the database")
            token = input("Token: ")
            prefix = input("Prefix: ")
            self.database.get_table("bot_config").add(token=token, prefix=prefix)
        self.token = self.database.get_table("bot_config").get_entry_by_row(0)["token"]
        prefix = self.database.get_table("bot_config").get_entry_by_row(0)["prefix"]
        super().__init__(command_prefix=prefix, assume_unsync_clock=True, *args, **kwargs)
        self.client = super()
        for extension in self.extensions:
            self.unload_extension(extension)

    async def load_cogs(self):
        for cog in self.cog_names:
            await self.load_extension(cog)

    def owner(self):
        return super().owner_id

    async def get_context(self, message, *, cls=PlexContext):
        ctx = await super().get_context(message, cls=cls)
        return ctx

    async def fetch_plex(self, guild: discord.Guild) -> PlexServer:
        """Allows for getting a plex instance for a guild if ctx is not available"""
        guild_id = guild.id
        if guild_id not in plex_servers:
            server_entry = self.database.get_table("plex_servers").get_row(guild_id=guild_id)
            if server_entry is None:
                raise ValueError("No plex server found for this guild")
            try:
                plex_servers[guild_id] = PlexServer(server_entry["server_url"], server_entry["server_token"])
                plex_servers[guild_id].baseurl = server_entry["server_url"]
                plex_servers[guild_id].token = server_entry["server_token"]
            except Exception:
                raise Exception("Invalid plex server credentials, or server is offline")
        if not hasattr(plex_servers[guild_id], "associations"):
            discord_associations.update({guild_id: DiscordAssociations(self, guild)})
            plex_servers[guild_id].associations = discord_associations[guild_id]
        if not hasattr(plex_servers[guild_id], "database"):
            plex_servers[guild_id].database = self.database
        return plex_servers[guild_id]

    async def on_ready(self):
        self.database.backup(target=self.backup_database, progress=self.db_backup_callback)
        logging.info(f"Logged in as \"{self.user.name}\" - {self.user.id}")
        logging.info(f"Discord.py API version: {discord.__version__}")
        for cog in self.cog_names:
            try:
                await self.load_extension(cog)
            except Exception as e:
                logging.error(f"Failed to load cog {cog}: {e}")

        # Run the on_ready functions for each loaded cog
        for cog in self.cogs.values():
            try:
                await cog.on_ready()
            except AttributeError:
                pass
            except Exception as e:
                logging.error(f"Failed to run on_ready for cog {cog}: {e}")
                logging.trace(e)

        for guild in self.guilds:
            await guild.chunk(cache=True)

        await self.change_presence(activity=discord.Game(name="PlexBot Startup"))

        # Establish a connection to the plex server for each guild

        for guild in self.guilds:
            try:
                await self.fetch_plex(guild)
            except Exception as e:
                logging.error(f"Failed to connect to plex server for guild \"{guild.name}\": {e}")
            else:
                logging.info(f"Connected to plex server for guild {guild.name}")

        # To get the activity message IDs and channel IDs
        # Print bot invite link to console
        perms = 469830672
        invite = oauth_url(super().user.id, permissions=discord.Permissions(perms))
        logging.info(f"Invite link: {invite}")
        logging.info(f"Prefix: {self.command_prefix}")

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


intents = discord.Intents.all()
try:
    plex_bot = PlexBot(intents=intents)
    # Print the intents we are using
    logging.info(f"Using intents: Members: {intents.members}, Presences: {intents.presences},"
                 f" Messages: {intents.messages}")
    plex_bot.run()
except discord.errors.PrivilegedIntentsRequired:
    logging.warning("Privileged intents are required to run this bot. Please enable them in the discord developer "
                    "portal.")
    plex_bot = PlexBot(intents=discord.Intents.default())
    plex_bot.run()
except Exception as e:
    logging.error(f"Failed to start bot: {e}")
    logging.trace(e)
    exit(1)
