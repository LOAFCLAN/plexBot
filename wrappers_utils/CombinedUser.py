import datetime

import discord
import plexapi

from loguru import logger as logging


class CombinedUser:
    class UnlinkedUserError(Exception):
        def __str__(self) -> str:
            return super().__str__()

    def __init__(self, plex_server, discord_member: discord.Member = None, plex_id: str = None, plex_email: str = None,
                 plex_username: str = None, plex_unknown: str = None):
        if plex_server is None:
            raise Exception("No plex server provided")
        self.plex_server = plex_server
        self.discord_id_only = False
        if isinstance(discord_member, int):
            # If we are passed a discord ID this means that there is a linked
            # but the user could not be found, we will then display the discord ID instead
            self.discord_id_only = True
        elif not isinstance(discord_member, discord.Member) and discord_member is not None:
            raise Exception("Discord member must be discord.Member, not %s" % type(discord_member))
        self.linked = False
        self.discord_member = discord_member
        self.plex_user = None
        self.plex_system_account = None
        self.__plex_id__ = plex_id
        self.__plex_email__ = plex_email
        self.__plex_username__ = plex_username
        self.__plex_unknown__ = plex_unknown

        # If we don't have any information about the plex account then we raise an exception
        if self.__plex_id__ is None and self.__plex_email__ is None and self.__plex_username__ is None \
                and self.__plex_unknown__ is None:
            raise CombinedUser.UnlinkedUserError(
                f"Cannot create CombinedUser from unlinked discord member {self.discord_member}")

        if self.__plex_id__ == plex_server.myPlexAccount().id:
            self.__plex_id__ = 1

        if not self._load_sys_user():
            return
        if not self._load_plex_user():
            return
        self.linked = True

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

    @property
    def plex_id(self):
        if self.plex_user is not None:
            return self.plex_user.id
        return self.__plex_id__

    @property
    def discord_id(self):
        if not self.linked:
            return None
        if self.discord_member is not None:
            return self.discord_member.id
        return None

    def display_name(self, plex_only=False, discord_only=False):
        if self.discord_id_only and not plex_only:
            return f"(ID: {self.discord_member})"
        elif self.discord_member is not None and not plex_only:
            return self.discord_member.display_name
        elif self.plex_user is not None and not discord_only:
            return self.plex_user.username
        elif self.plex_system_account is not None and not discord_only:
            return self.plex_system_account.name
        else:
            if plex_only:
                return "No linked plex account"
            elif discord_only and self.discord_id_only:
                return f"(ID: {self.discord_member})"
            elif discord_only:
                return "No linked discord account"

    def mention(self, plex_only=False, discord_only=False):
        if self.discord_member is not None and not plex_only and not self.discord_id_only:
            return self.discord_member.mention
        elif self.plex_user is not None and not discord_only:
            return f"`{self.plex_user.username}`"
        elif self.plex_system_account is not None and not discord_only:
            return f"`{self.plex_user.name}`"
        else:
            return "Unknown"

    def full_discord_username(self):
        if self.discord_member is not None and not self.discord_id_only:
            return f"{self.discord_member.name}#{self.discord_member.discriminator}"
        else:
            return "unknown#0000"

    def avatar_url(self, plex_only=False, discord_only=False):
        if self.discord_member is not None and not plex_only and not self.discord_id_only:
            return self.discord_member.display_avatar.url
        elif self.plex_user is not None and not discord_only:
            return self.plex_user.thumb
        else:
            return ""

    @property
    def account_id(self):
        if self.plex_user is not None:
            return self.plex_user.id
        return self.__plex_id__

    def id(self, plex_only=False, discord_only=False):
        if self.discord_id_only and not plex_only:
            return self.discord_member
        elif self.discord_member is not None and not plex_only:
            return self.discord_member.id
        elif self.plex_user is not None and not discord_only:
            return self.plex_user.id
        elif self.plex_system_account is not None and not discord_only:
            return self.plex_system_account.id
        else:
            return ""

    @property
    def devices(self):
        """Sort through all plex devices and return those that are associated with this user"""
        if self.plex_user is None:
            return []
        else:
            cursor = self.plex_server.database.execute("SELECT * FROM plex_devices "
                                                       "WHERE account_id = ? AND last_seen < ? ORDER BY last_seen",
                                                       (self.plex_system_account.id, datetime.datetime.now()
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
            # Sort the devices by last seen
            devices.sort(key=lambda x: x.last_seen, reverse=True)
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
        elif isinstance(other, discord.Member):
            return self.discord_member == other
        elif isinstance(other, plexapi.server.SystemAccount):
            return self.plex_system_account.id == other.id
        elif isinstance(other, plexapi.myplex.MyPlexUser):
            return self.plex_user.id == other.id
        elif isinstance(other, str):
            return self._compare_plex_info(other)
        elif isinstance(other, int):
            # Check if the other is a discord id
            if self.discord_member is not None:
                return self.discord_member.id == other
            elif self.plex_user is not None:
                return self.plex_user.id == other
            elif self.plex_system_account is not None:
                return self.plex_system_account.id == other
            else:
                return False
        else:
            raise TypeError(f"Can only compare PlexUser, SystemAccount, or str, not {type(other)}")

    def __object__(self):
        return self.discord_member

    def __str__(self):
        return_str = "("
        return_str += f"Discord: {self.discord_member.name}; " if self.discord_member else "Discord: None; "
        return_str += f"Plex: {self.plex_user.username}; " if self.plex_user else "Plex: None; "
        return_str += f"PlexSys: {self.plex_system_account.id}" if self.plex_system_account else "PlexSys: None"
        return_str += ")"
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
