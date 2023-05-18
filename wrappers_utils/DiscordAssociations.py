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
        self.associations = []
        self.bot.loop.create_task(self.load_associations())

    async def load_associations(self) -> None:
        logging.info(f"Loading associations for {self.guild.name}")
        await self.bot.wait_until_ready()  # Wait until the bot is ready for API calls
        self.plex_server = await self.bot.fetch_plex(self.guild)
        await self.plex_server.wait_until_ready()  # Wait until the plex server is ready for API calls
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
