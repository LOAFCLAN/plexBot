import typing

import discord
from loguru import logger as logging

from wrappers_utils.CombinedUser import CombinedUser


class DiscordAssociations:

    def __init__(self, bot, guild: discord.Guild):
        self.bot = bot
        if not isinstance(guild, discord.Guild):
            raise Exception("Guild must be discord.Guild, not %s" % type(guild))
        self.guild = guild
        self.plex_server = None
        self.ready = False
        self.discord_users = {}  # type: typing.Dict[int, discord.Member]
        self.associations = []
        self.bot.loop.create_task(self.load_associations())

    async def on_member_join(self, member: discord.Member):
        logging.info(f"{member} joined {self.guild.name}")
        self.discord_users[member.id] = member

    async def load_associations(self) -> None:
        logging.info(f"Loading associations for {self.guild.name}")
        await self.bot.wait_until_ready()  # Wait until the bot is ready for API calls
        self.plex_server = await self.bot.fetch_plex(self.guild)
        await self.plex_server.wait_until_ready()  # Wait until the plex server is ready for API calls

        # Chunk all users from all guilds into the bot's cache
        for guild in self.bot.guilds:
            await guild.chunk(cache=True)
        logging.info(f"Loaded {len(self.bot.users)} users into cache")
        # Copy all users from the bot's cache into the discord_users dict
        for member in self.guild.members:
            self.discord_users[member.id] = member
        return
        # cursor = self.bot.database.execute("SELECT * FROM discord_associations WHERE guild_id = ?", (self.guild.id,))
        table = self.bot.database.get_table("discord_associations")
        association_ids = []
        for row in table.get_rows(guild_id=self.guild.id):
            try:
                member = await self.guild.fetch_member(row[1])
            except discord.NotFound:
                member = row[1]
            try:
                self.associations.append(CombinedUser(plex_server=self.plex_server,
                                                      discord_member=member,
                                                      plex_id=row[2], plex_email=row[3], plex_username=row[4]))
                association_ids.append(row[2])
            except Exception as e:
                logging.exception(e)
        users = self.plex_server.myPlexAccount().users()
        for user in users:
            if user.id not in association_ids:
                try:
                    self.associations.append(CombinedUser(plex_server=self.plex_server,
                                                          plex_id=user.id, plex_email=user.email,
                                                          plex_username=user.username))
                except Exception as e:
                    logging.exception(e)

        self.ready = True
        logging.info(f"Loaded {len(self.associations)} associations for"
                     f" {self.guild.name} ({self.plex_server.friendlyName})")

    def lookup_association(self, discord_member=None,
                             plex_id=None, plex_email=None, plex_username=None, plex_unknown=None):
        # For each kwarg check if that exists in the database and if so return that entry
        table = self.bot.database.get_table("discord_associations")
        if discord_member is not None:
            if result := table.get_row(discord_user_id=discord_member.id):
                return result
        if plex_id is not None:
            if result := table.get_row(plex_id=plex_id):
                return result
        if plex_email is not None:
            if result := table.get_row(plex_email=plex_email):
                return result
        if plex_username is not None:
            if result := table.get_row(plex_username=plex_username):
                return result
        if plex_unknown is not None:
            if result := table.get_row(plex_username=plex_unknown):
                return result
            if result := table.get_row(plex_email=plex_unknown):
                return result
            try:
                if result := table.get_row(plex_id=int(plex_unknown)):
                    return result
            except ValueError:
                pass
        return None

    def create_combined_user(self, discord_member=None,
                             plex_id=None, plex_email=None, plex_username=None, plex_unknown=None) -> CombinedUser:
        logging.info(f"Creating new association for user who was not here at startup: "
                     f"PID:{plex_id} PEM:{plex_email} PUN:{plex_username} PUK:{plex_unknown} DSC:{discord_member}")
        association = self.lookup_association(discord_member=discord_member,
                                              plex_id=plex_id, plex_email=plex_email, plex_username=plex_username,
                                              plex_unknown=plex_unknown)
        if association is not None:
            if association['discord_user_id'] in self.discord_users:
                member = self.discord_users[association['discord_user_id']]
            else:
                member = association['discord_user_id']
            user = CombinedUser(self.plex_server, member, association['plex_id'],
                                association['plex_email'], association['plex_username'])
            self.associations.append(user)
        # If there is no association one can be created as long as there is plex information
        elif plex_id is not None or plex_email is not None or plex_username is not None or plex_unknown is not None:
            user = CombinedUser(self.plex_server, plex_id=plex_id, plex_email=plex_email, plex_username=plex_username,
                                plex_unknown=plex_unknown)
            self.associations.append(user)
        else:
            raise Exception("No association could be created")
        return user

    def get_discord_association(self, discord_member: discord.Member, no_create=False) -> CombinedUser or None:
        """Returns the plex user associated with the discord member"""
        for association in self.associations:
            if association.discord_member == discord_member:
                return association
        if no_create:
            return None
        return self.create_combined_user(discord_member=discord_member)

    def get_plex_association(self, plex_user: str, no_create=False) -> CombinedUser or None:
        """Returns the discord member associated with the plex user"""
        for association in self.associations:
            if association == plex_user:
                return association
        if no_create:
            return None
        return self.create_combined_user(plex_unknown=plex_user)

    def get(self, search: typing.Union[discord.Member, str, int], no_create=False) -> CombinedUser:
        if isinstance(search, discord.Member):
            return self.get_discord_association(search)
        elif isinstance(search, str) or isinstance(search, int):
            if result := self.get_plex_association(str(search), no_create=True):
                return result
            # Check if the search can be converted to an int
            try:
                discord_user = self.guild.get_member(int(search))
                if discord_user is not None:
                    return self.get_discord_association(discord_user, no_create=True)
            except ValueError:
                pass
            if no_create:
                return None
            return self.create_combined_user(plex_unknown=str(search))
        else:
            raise Exception(f"Invalid type for search, must be discord.Member or str not {type(search)}")

    def add_association(self, plex_server, discord_member: discord.Member,
                        plex_id: str, plex_email: str, plex_username: str) -> None:
        self.associations.append(CombinedUser(plex_server, discord_member, plex_id, plex_email, plex_username))
        table = self.bot.database.get_table("discord_associations")
        table.update_or_add(guild_id=self.guild.id, discord_user_id=discord_member.id, plex_id=plex_id,
                            plex_email=plex_email, plex_username=plex_username)
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
        association = self.get(plex_user)
        if association is not None:
            return association.discord_member.mention
        return plex_user

    def display_name(self, plex_user: typing.Union[str, int]) -> str:
        association = self.get(plex_user)
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

    def __iter__(self) -> typing.Iterator[CombinedUser]:
        return iter(self.associations)

    def __len__(self):
        return len(self.associations)

    def __contains__(self, item):
        for association in self.associations:
            if association == item:
                return True
        return False
