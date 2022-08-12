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
                return None
            row = cursor.fetchone()
            try:
                plex_servers[guild_id] = PlexServer(row[1], row[2])
            except Exception as e:
                raise Exception("Invalid plex server credentials, or server is offline"
                                "\nTraceback: %s" % traceback.format_exc()[1800:])
        if not hasattr(plex_servers[guild_id], "associations"):
            discord_associations.update({guild_id: DiscordAssociations(self.bot, self.guild)})
            plex_servers[guild_id].associations = discord_associations[guild_id]
        if not hasattr(plex_servers[guild_id], "database"):
            plex_servers[guild_id].database = self.bot.database

        return plex_servers[guild_id]

    @property
    def plex(self):
        return self._get_plex()

    @property
    def plex_host(self):
        return self._get_plex().myPlexAccount()


class CombinedUser:

    def __init__(self, plex_server, discord_member: discord.Member = None, plex_id: str = None, plex_email: str = None,
                 plex_username: str = None, plex_unknown: str = None):
        if plex_server is None:
            raise Exception("No plex server provided")
        self.plex_server = plex_server
        if not isinstance(discord_member, discord.Member) and discord_member is not None:
            raise Exception("Discord member must be discord.Member, not %s" % type(discord_member))
        self.discord_member = discord_member
        self.plex_user = None
        self.plex_system_account = None
        self.__plex_id__ = plex_id
        self.__plex_email__ = plex_email
        self.__plex_username__ = plex_username
        self.__plex_unknown__ = plex_unknown

        # If we don't have any information about the plex account then we raise an exception
        if self.__plex_id__ is None and self.__plex_email__ is None and self.__plex_username__ is None \
                and self.__plex_unknown__ is None and self.discord_member is None:
            raise Exception("Insufficient information provided to create a CombinedUser")

        if plex_server.myPlexAccount().id == self.__plex_id__:
            self.__plex_id__ = 1

        if not self._load_sys_user():
            raise Exception(f"Could not find plex user account for {self.discord_member}")
        if not self._load_plex_user():
            print("Idfk")

    def _load_sys_user(self) -> bool:
        if self.__plex_unknown__ is not None:
            for user in self.plex_server.systemAccounts():
                if user.name == self.__plex_unknown__:
                    self.plex_system_account = user
                    return True
                elif str(user.id) == self.__plex_unknown__:
                    self.plex_system_account = user
                    return True
        if self.__plex_username__ is not None:
            for user in self.plex_server.systemAccounts():
                if user.name == self.__plex_username__:
                    self.plex_system_account = user
                    return True
        if self.__plex_id__ is not None:
            if self.plex_server.systemAccount(self.__plex_id__):
                self.plex_system_account = self.plex_server.systemAccount(self.__plex_id__)
                return True
        if self.__plex_email__ is not None:
            for user in self.plex_server.systemAccounts():
                if user.email == self.__plex_email__:
                    self.plex_system_account = user
                    return True
        return False

    def _load_plex_user(self) -> bool:
        host = self.plex_server.myPlexAccount()
        if self.plex_system_account.id == 1:
            self.plex_user = host  # Nick btw I hate you
            return True
        if user := host.user(self.plex_system_account.id):
            self.plex_user = user
            return True
        return False

    def display_name(self, plex_only=False, discord_only=False):
        if self.discord_member is not None and not plex_only:
            return self.discord_member.display_name
        elif self.plex_user is not None and not discord_only:
            return self.plex_user.username
        elif self.plex_system_account is not None and not discord_only:
            return self.plex_system_account.name
        else:
            return "Unknown" if not discord_only else "No linked discord account" if not plex_only \
                else "No linked plex account"

    def mention(self, plex_only=False, discord_only=False):
        if self.discord_member is not None and not plex_only:
            return self.discord_member.mention
        elif self.plex_user is not None and not discord_only:
            return f"`{self.plex_user.username}`"
        elif self.plex_system_account is not None and not discord_only:
            return f"`{self.plex_user.name}`"
        else:
            return "Unknown"

    def avatar_url(self, plex_only=False, discord_only=False):
        if self.discord_member is not None and not plex_only:
            return self.discord_member.avatar_url
        elif self.plex_user is not None and not discord_only:
            return self.plex_user.thumb
        else:
            return ""

    @property
    def devices(self):
        """Sort through all plex devices and return those that are associated with this user"""
        if self.plex_user is None:
            return []
        else:
            cursor = self.plex_server.database.execute("SELECT * FROM plex_devices "
                                                       "WHERE account_id = ? AND last_seen < ? ORDER BY last_seen DESC",
                                                       (self.plex_user.id, datetime.datetime.now()
                                                        - datetime.timedelta(days=7)))
            all_devices = self.plex_server.systemDevices()
            rows = cursor.fetchall()
            ids = [row[1] for row in rows]
            devices = [device for device in all_devices if device.clientIdentifier in ids]
            # Add a last seen attribute to the devices
            for device in devices:
                for row in rows:
                    if device.clientIdentifier == row[1]:
                        device.last_seen = row[2]
            return devices

    def _compare_plex_info(self, other: str):
        if self.plex_user is not None:
            if self.plex_user.username == other:
                return True
            elif str(self.plex_user.id) == other:
                return True
            elif self.plex_user.email == other:
                return True
        if self.plex_system_account is not None:
            if self.plex_system_account.name == other:
                return True
            elif str(self.plex_system_account.id) == other:
                return True
        return False

    def __eq__(self, other):
        if isinstance(other, CombinedUser):
            return self.__plex_id__ == other.__plex_id__
        elif isinstance(other, plexapi.server.SystemAccount):
            return self.__plex_id__ == other.id
        elif isinstance(other, plexapi.myplex.MyPlexUser):
            return self.__plex_id__ == other.id
        elif isinstance(other, str):
            return self._compare_plex_info(other)
        else:
            raise TypeError(f"Can only compare PlexUser, SystemAccount, or str, not {type(other)}")

    def __object__(self):
        return self.discord_member

    def __str__(self):
        return_str = ""
        return_str += f"Discord: {self.discord_member.name}; " if self.discord_member else "Discord: None; "
        return_str += f"Plex: {self.plex_user.username}; " if self.plex_user else "Plex: None; "
        return_str += f"PlexSys: {self.plex_system_account.id}; " if self.plex_system_account else "PlexSys: None; "
        return return_str

    def __repr__(self):
        return self.__str__()

    def __hash__(self):
        return hash((self.discord_member, self.__plex_id__))

    def __iter__(self):
        yield self

    def __next__(self):
        return self

    def __getitem__(self, item):
        if item == "plex_id":
            return self.__plex_id__
        elif item == "plex_email":
            return self.__plex_email__
        elif item == "plex_username":
            return self.__plex_username__
        elif item == "plex_unknown":
            return self.__plex_unknown__
        else:
            raise AttributeError(f"No attribute {item}")

    def __contains__(self, item):
        if item == "plex_id":
            return self.__plex_id__
        elif item == "plex_email":
            return self.__plex_email__
        elif item == "plex_username":
            return self.__plex_username__
        elif item == "plex_unknown":
            return self.__plex_unknown__
        else:
            raise AttributeError(f"No attribute {item}")


class DiscordAssociations:

    def __init__(self, bot, guild: discord.Guild):
        self.bot = bot
        if not isinstance(guild, discord.Guild):
            raise Exception("Guild must be discord.Guild, not %s" % type(guild))
        self.guild = guild
        self.plex_server = None
        self.ready = False
        self.associations = []
        self.bot.loop.create_task(self.load_associations())

    async def load_associations(self) -> None:
        print(f"Loading associations for {self.guild}")
        await self.bot.wait_until_ready()
        self.plex_server = await self.bot.fetch_plex(self.guild)
        cursor = self.bot.database.execute("SELECT * FROM discord_associations WHERE guild_id = ?", (self.guild.id,))
        for row in cursor:
            member = await self.guild.fetch_member(row[1])
            self.associations.append(CombinedUser(plex_server=self.plex_server,
                                                  discord_member=member,
                                                  plex_id=row[2], plex_email=row[3], plex_username=row[4]))
        self.ready = True

    def get_discord_association(self, discord_member: discord.Member) -> CombinedUser:
        """Returns the plex user associated with the discord member"""
        for association in self.associations:
            if association.discord_member == discord_member:
                return association
        return CombinedUser(plex_server=self.plex_server, discord_member=discord_member)

    def get_plex_association(self, plex_user: str) -> CombinedUser:
        """Returns the discord member associated with the plex user"""
        for association in self.associations:
            if association == plex_user:
                return association
        return CombinedUser(plex_server=self.plex_server, plex_unknown=plex_user)

    def get(self, search: typing.Union[discord.Member, str, int]) -> CombinedUser:
        if isinstance(search, discord.Member):
            return self.get_discord_association(search)
        elif isinstance(search, str):
            return self.get_plex_association(search)
        elif isinstance(search, int):
            return self.get_plex_association(str(search))
        else:
            raise Exception(f"Invalid type for search, must be discord.Member or str not {type(search)}")

    def add_association(self, plex_server, discord_member: discord.Member,
                        plex_id: str, plex_email: str, plex_username: str) -> None:
        self.associations.append(CombinedUser(plex_server, discord_member, plex_id, plex_email, plex_username))
        self.bot.database.execute('''INSERT OR REPLACE INTO discord_associations VALUES (?, ?, ?, ?, ?)''',
                                  (self.guild.id,
                                   discord_member.id,
                                   plex_id,
                                   plex_username,
                                   plex_email))
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
            return association.display_name()
        return plex_user

    def __str__(self):
        return f"{self.guild.name}'s Discord Associations containing {len(self.associations)} associations"

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        return self.guild == other.guild

    def __hash__(self):
        return hash(self.associations)

    def __iter__(self) -> Iterator[CombinedUser]:
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

        print(f"Creating SessionWatcher for {session.title} ({session.year}) on {server.friendlyName}")

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

        # Get the hardware ID of the device that is playing the video
        device_id = session.player.machineIdentifier
        user_id = session.player.userID
        self.server.database.execute("INSERT OR REPLACE INTO plex_devices VALUES (?, ?, ?);",
                                     (user_id, device_id, datetime.datetime.now().timestamp()))
        self.server.database.commit()

        self.end_offset = self.session.viewOffset

        self.alive_time = datetime.datetime.utcnow()

    async def refresh_session(self, session: plexapi.video.Video) -> None:
        self.session = session
        self.media = session.media[0]

        if not self.session.isFullObject:
            self.session.reload(checkFiles=False)
            if not self.session.isFullObject:
                raise Exception("Session is still partial")

        self.end_offset = self.session.viewOffset

    async def session_expired(self):
        await self.callback(self)

    def __str__(self):
        return f"{self.session.title}@{self.server.friendlyName}"

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
            try:
                sessions = self.server.sessions()
                for session in sessions:
                    try:
                        already_exists = False
                        for watcher in self.watchers:
                            if watcher.session == session and session.title == watcher.initial_session.title:
                                await watcher.refresh_session(session)
                                already_exists = True
                                break
                        if not already_exists:
                            watcher = SessionWatcher(session, self.server, self.callback)
                            self.watchers.append(watcher)
                    except Exception as e:
                        print(f"Error refreshing session {session.title}: {e}\n{traceback.format_exc()}")

                for watcher in self.watchers:
                    try:
                        session_still_exists = False
                        for session in sessions:
                            if watcher.session == session and session.title == watcher.initial_session.title:
                                session_still_exists = True
                                break
                        if not session_still_exists:
                            await watcher.session_expired()
                    except Exception as e:
                        print(f"Error checking for continued existence of session "
                              f"{watcher.session.title}: {e}\n{traceback.format_exc()}")

            except Exception as e:
                print(f"Error checking sessions: {e}\n{traceback.format_exc()}")
            finally:
                await asyncio.sleep(1.5)

    async def callback(self, watcher: SessionWatcher):
        try:
            await self.callbacktoback(watcher, self.channel)
        except Exception as e:
            print(f"Error in callback: {e}\n{traceback.format_exc()}")
        finally:
            self.watchers.remove(watcher)
