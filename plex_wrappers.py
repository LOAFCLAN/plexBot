import asyncio
import datetime
import traceback
import typing
from copy import copy, deepcopy
from typing import Iterator

import discord
import plexapi
from discord.ext import commands
from plexapi.server import PlexServer

plex_servers = {}
discord_associations = {}


class PlexContext(commands.Context):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _get_plex(self):
        guild_id = self.guild.id
        if guild_id not in plex_servers:
            cursor = self.bot.database.execute("SELECT * FROM plex_servers WHERE guild_id = ?", (guild_id,))
            if cursor.rowcount == 0:
                raise Exception("No plex server found for this guild")
            row = cursor.fetchone()
            try:
                plex_servers[guild_id] = PlexServer(row[1], row[2])
            except Exception as e:
                raise Exception("Invalid plex server credentials, or server is offline"
                                "\nTraceback: %s" % traceback.format_exc()[1800:])
        if not hasattr(plex_servers[guild_id], "associations"):
            discord_associations.update({guild_id: DiscordAssociations(self.bot, self.guild)})
            plex_servers[guild_id].associations = discord_associations[guild_id]

        return plex_servers[guild_id]

    @property
    def plex(self):
        return self._get_plex()

    @property
    def plex_host(self):
        return self._get_plex().myPlexAccount()


class DiscordAssociation:

    def __init__(self, discord_member: discord.Member, plex_id: str, plex_email: str, plex_username: str):
        if not isinstance(discord_member, discord.Member):
            raise Exception("Discord member must be discord.Member, not %s" % type(discord_member))
        self.discord_member = discord_member
        self.plex_id = plex_id
        self.plex_email = plex_email
        self.plex_username = plex_username

    def compare_plex_user(self, other):
        return self.plex_id == other or self.plex_email == other or self.plex_username == other

    def __object__(self):
        return self.discord_member

    def __str__(self):
        return "Discord: %s, Plex: %s" % (self.discord_member.name, self.plex_id)

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other) -> bool:
        if isinstance(other, DiscordAssociation):
            # print(f"Comparing {self} to {other}, DiscordAssociation")
            return self.discord_member == other.discord_member and self.plex_id == other.plex_id
        elif isinstance(other, discord.Member):
            # print(f"Comparing {self} to {other}, discord.Member")
            return self.discord_member.id == other.id
        elif isinstance(other, discord.User):
            # print(f"Comparing {self} to {other}, discord.User")
            return self.discord_member.id == other.id
        elif isinstance(other, str):
            # print(f"Comparing {self} to {other}, str")
            return self.compare_plex_user(other)
        else:
            raise Exception(f"Invalid type for comparison, must be DiscordAssociation, "
                            f"discord.Member, discord.User, or str. Not {type(other)}")

    def __contains__(self, item):
        return self == item

    def __hash__(self):
        return hash((self.discord_member, self.plex_id))

    def __iter__(self):
        yield self


class DiscordAssociations:

    def __init__(self, bot, guild: discord.Guild):
        self.bot = bot
        if not isinstance(guild, discord.Guild):
            raise Exception("Guild must be discord.Guild, not %s" % type(guild))
        self.guild = guild
        self.associations = []
        self.load_associations()

    def load_associations(self) -> None:
        cursor = self.bot.database.execute("SELECT * FROM discord_associations WHERE guild_id = ?", (self.guild.id,))
        for row in cursor:
            member = self.guild.get_member(row[1])
            if member is None:
                continue
            self.associations.append(DiscordAssociation(member, row[2], row[3], row[4]))

    def get_discord_association(self, discord_member: discord.Member) -> DiscordAssociation:
        """Returns the plex user associated with the discord member"""
        for association in self.associations:
            if association.discord_member == discord_member:
                return association
        return None

    def get_plex_association(self, plex_user: str) -> DiscordAssociation:
        """Returns the discord member associated with the plex user"""
        for association in self.associations:
            if association.compare_plex_user(plex_user):
                return association
        return None

    def add_association(self, discord_member: discord.Member,
                        plex_id: str, plex_email: str, plex_username: str) -> None:
        self.associations.append(DiscordAssociation(discord_member, plex_id, plex_email, plex_username))
        self.bot.database.execute("INSERT INTO discord_associations VALUES (?, ?, ?, ?, ?)",
                                  (self.guild.id,
                                   discord_member.id,
                                   plex_id,
                                   plex_email,
                                   plex_username))
        self.bot.database.commit()

    def remove_association(self, discord_member: discord.Member) -> bool:
        association = self.get_discord_association(discord_member)
        if association is not None:
            self.associations.remove(association)
            self.bot.database.execute("DELETE FROM discord_associations WHERE guild_id = ? AND discord_user_id = ?",
                                      (self.guild.id, discord_member.id))
            self.bot.database.commit()
            return True
        return False

    def mention(self, plex_user: str) -> str:
        association = self.get_plex_association(plex_user)
        if association is not None:
            return association.discord_member.mention
        return plex_user

    def display_name(self, plex_user: str) -> str:
        association = self.get_plex_association(plex_user)
        if association is not None:
            return association.discord_member.display_name
        return plex_user

    def __str__(self):
        return f"{self.guild.name}'s Discord Associations containing {len(self.associations)} associations"

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        return self.guild == other.guild

    def __hash__(self):
        return hash(self.associations)

    def __iter__(self) -> Iterator[DiscordAssociation]:
        return iter(self.associations)

    def __len__(self):
        return len(self.associations)

    def __contains__(self, item):
        for association in self.associations:
            if association == item:
                return True
        return False


# class PlexSessionSnapshot:
#
#     def __init__(self, session: plexapi.video.Video):
#         self.view_offset = deepcopy(session.viewOffset)
#         self.duration = deepcopy(session.duration)


class SessionWatcher:

    def __init__(self, session: plexapi.video.Video, server, callback) -> None:

        self.callback = callback
        self.server = server

        if not session.isFullObject:
            session.reload(checkFiles=False)
            if not session.isFullObject:
                raise Exception("Session is still partial")

        media = session.media[0]

        # self.initial_media = copy(media)
        self.media = media
        self.initial_session = session
        self.session = session

        self.end_offset = -1

        self.alive_time = datetime.datetime.utcnow()

    async def session_check(self):
        print(f"Starting session check for {self.session.title}")
        while True:
            # Make sure the session still exists
            if self.session not in self.server.sessions():
                print(f"Session {self.session.title} no longer exists")
                await self.callback(self)
                break

            if not self.session.isFullObject:
                self.session.reload(checkFiles=False)
                if not self.session.isFullObject:
                    raise Exception("Session is still partial")

            self.end_offset = self.session.viewOffset
            await asyncio.sleep(1)

    def refresh_session(self, session: plexapi.video.Video) -> None:
        self.session = session
        self.media = session.media[0]

    def __str__(self):
        return f"{self.media.title} - {self.session.player.title}"

    def __eq__(self, other):
        print(f"Comparing {self} to {other}, Type: {type(other)}")
        if isinstance(other, SessionWatcher):
            return self.session == other.session
        elif isinstance(other, plexapi.media.Session):
            return self.session == other
        elif isinstance(other, plexapi.media.Media):
            return self.media == other
        elif isinstance(other, plexapi.video.Video):
            return self.media == other
        elif isinstance(other, list):
            return False
        elif other is None:
            return False
        else:
            raise TypeError(f"Invalid type for comparison, must be SessionWatcher, "
                            f"plexapi.media.Session, or plexapi.media.Media. Not {type(other)}")

    def __iter__(self):
        yield self


class SessionChangeWatcher:
    """Binds to a plexapi.Server and fires events when sessions start or stop"""

    def __init__(self, server_object: plexapi.server, callback: typing.Callable, channel: discord.TextChannel) -> None:
        self.server = server_object
        self.watchers = []
        self.callbacktoback = callback
        self.channel = channel
        asyncio.get_event_loop().create_task(self.observer())

    async def observer(self):
        while True:
            sessions = self.server.sessions()
            for session in sessions:
                already_exists = False
                for watcher in self.watchers:
                    if watcher.session == session and session.title == watcher.initial_session.title:
                        watcher.refresh_session(session)
                        already_exists = True
                        break
                if not already_exists:
                    watcher = SessionWatcher(session, self.server, self.callback)
                    self.watchers.append(watcher)
                    asyncio.get_event_loop().create_task(watcher.session_check())
            await asyncio.sleep(1)

    async def callback(self, watcher: SessionWatcher):
        await self.callbacktoback(watcher, self.channel)
        self.watchers.remove(watcher)
