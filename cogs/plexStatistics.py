import asyncio
import datetime
import random

import discord
import plexapi.video
from discord import Interaction, ButtonStyle, ActionRow
from discord.ext import commands
from discord.ext.commands import Cog, command, has_permissions
# import custom_dpy_overrides
# from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction, ActionRow
from discord.ui import Button, View, Select

from plex_wrappers import SessionChangeWatcher, SessionWatcher
from utils import base_info_layer, get_season, get_episode, cleanup_url, text_progress_bar_maker, stringify, \
    base_user_layer

from loguru import logger as logging


class PlexStatistics(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.history_table = self.bot.database.get_table("plex_history_messages")

    @commands.command(name="global_stats")
    async def global_stats(self, ctx):
        """Gets global watch statistics for the server, such as total watch time, total number of episodes watched,
           most popular movie, show etc."""
        total_watch_time = self.bot.database.get("SELECT SUM(watch_time) / 1000 FROM main.plex_history_messages")[0][0]
        total_sessions = self.bot.database.get("SELECT COUNT(*) FROM main.plex_history_messages")[0][0]
        # Get the most popular movies by watch time
        most_popular_movies_time = self.bot.database.get("SELECT title, SUM(watch_time) / 1000 "
                                                         "FROM main.plex_history_messages WHERE media_type = 'movie' "
                                                         "GROUP BY title ORDER BY SUM(watch_time) DESC LIMIT 5")
        # Get the most popular shows by watch time
        most_popular_shows_time = self.bot.database.get("SELECT title, SUM(watch_time) / 1000 "
                                                        "FROM main.plex_history_messages WHERE media_type = 'episode' "
                                                        "GROUP BY title ORDER BY SUM(watch_time) DESC LIMIT 5")
        # Get the most popular episodes by watch time (this is a bit more complicated)
        most_popular_episodes_time = self.bot.database.get("SELECT title, season_num, ep_num, SUM(watch_time) / 1000 "
                                                           "FROM main.plex_history_messages"
                                                           " WHERE media_type = 'episode' "
                                                           "GROUP BY title, season_num, ep_num "
                                                           "ORDER BY SUM(watch_time) DESC LIMIT 5")

        # Get the most popular movies by number of plays
        most_popular_movies_plays = self.bot.database.get("SELECT title, COUNT(*) "
                                                              "FROM main.plex_history_messages WHERE media_type = 'movie' "
                                                                "GROUP BY title ORDER BY COUNT(*) DESC LIMIT 5")
        # Get the most popular shows by number of plays
        most_popular_shows_plays = self.bot.database.get("SELECT title, COUNT(*) "
                                                                "FROM main.plex_history_messages WHERE media_type = 'episode' "
                                                                "GROUP BY title ORDER BY COUNT(*) DESC LIMIT 5")
        # Get the most popular episodes by number of plays (this is a bit more complicated)
        most_popular_episodes_plays = self.bot.database.get("SELECT title, season_num, ep_num, COUNT(*) "
                                                                     "FROM main.plex_history_messages"
                                                                        " WHERE media_type = 'episode' "
                                                                        "GROUP BY title, season_num, ep_num "
                                                                        "ORDER BY COUNT(*) DESC LIMIT 5")

        # Format the data into a nice embed
        embed = discord.Embed(title="Global Plex Statistics",
                              description=f"Globally, {total_sessions} sessions have been logged,"
                                          f" totalling {datetime.timedelta(seconds=round(total_watch_time))}"
                                          f" of watch time.",
                              color=0x00ff00)
        embed.add_field(name="Most Popular Movies by Watch Time",
                        value="\n".join([f"`{i + 1}`. `{movie[0]}` - {datetime.timedelta(seconds=round(movie[1]))}"
                                         for i, movie in enumerate(most_popular_movies_time)]), inline=False)

        embed.add_field(name="Most Popular Movies by Number of Plays",
                        value="\n".join([f"`{i + 1}`. `{movie[0]}` - {movie[1]} plays"
                                            for i, movie in enumerate(most_popular_movies_plays)]), inline=False)

        embed.add_field(name="Most Popular Shows by Watch Time",
                        value="\n".join([f"`{i + 1}`. `{show[0]}` - {datetime.timedelta(seconds=round(show[1]))}"
                                         for i, show in enumerate(most_popular_shows_time)]), inline=False)

        embed.add_field(name="Most Popular Shows by Number of Plays",
                        value="\n".join([f"`{i + 1}`. `{show[0]}` - {show[1]} plays"
                                            for i, show in enumerate(most_popular_shows_plays)]), inline=False)

        embed.add_field(name="Most Popular Episodes by Watch Time",
                        value="\n".join([f"`{i + 1}`. `{episode[0]} - S{episode[1]}E{episode[2]}` - "
                                         f"{datetime.timedelta(seconds=round(episode[3]))}"
                                         for i, episode in enumerate(most_popular_episodes_time)]), inline=False)

        embed.add_field(name="Most Popular Episodes by Number of Plays",
                        value="\n".join([f"`{i + 1}`. `{episode[0]} - S{episode[1]}E{episode[2]}` - "
                                            f"{episode[3]} plays"
                                            for i, episode in enumerate(most_popular_episodes_plays)]), inline=False)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PlexStatistics(bot))
    logging.info("PlexStatistics Loaded Successfully")
