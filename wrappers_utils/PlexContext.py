import traceback

from discord.ext import commands
from plexapi.server import PlexServer

from wrappers_utils.DiscordAssociations import DiscordAssociations

plex_servers = {}
discord_associations = {}


class PlexContext(commands.Context):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.table = self.bot.database.get_table("plex_servers")

    def _get_plex(self):
        guild_id = self.guild.id
        if guild_id not in plex_servers:
            row = self.table.get_row(guild_id=guild_id)
            if row:
                try:
                    plex_servers[guild_id] = PlexServer(row["server_url"], row["token"])
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
