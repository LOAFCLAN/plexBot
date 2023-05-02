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

        # Format the data into a nice embed
        embed = discord.Embed(title="Global Plex Statistics",
                              description=f"Globally, {total_sessions} sessions have been logged,"
                                          f" totalling {datetime.timedelta(seconds=round(total_watch_time))}"
                                          f" of watch time.",
                              color=0x00ff00)
        embed.add_field(name="Most Popular Movies by Watch Time",
                        value="\n".join([f"`{i + 1}`. `{movie[0]}` - {datetime.timedelta(seconds=round(movie[1]))}"
                                         for i, movie in enumerate(most_popular_movies_time)]), inline=False)

        embed.add_field(name="Most Popular Shows by Watch Time",
                        value="\n".join([f"`{i + 1}`. `{show[0]}` - {datetime.timedelta(seconds=round(show[1]))}"
                                         for i, show in enumerate(most_popular_shows_time)]), inline=False)

        embed.add_field(name="Most Popular Episodes by Watch Time",
                        value="\n".join([f"`{i + 1}`. `{episode[0]} - S{episode[1]}E{episode[2]}` - "
                                         f"{datetime.timedelta(seconds=round(episode[3]))}"
                                         for i, episode in enumerate(most_popular_episodes_time)]), inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="top_movies")
    async def top_movies(self, ctx):
        """Gets the top 6 movies"""
        # We do this all in database because it's much faster than doing it in python

        # A movie's score is based on the number of different users who have watched it, and the number of times
        # each user has watched it (so if a movie has been watched 10 times by 1 user, it will have a lower score
        # than a movie that has been watched 10 times by 10 users)
        self.bot.database.create_function("get_movie_score", 2, lambda x: self.bot.database.get(
            "SELECT COUNT(DISTINCT account_ID) * COUNT(*) FROM main.plex_history_messages "
            "WHERE title = ? and media_year = ? and media_type = 'movie'", x)[0][0])

        # Get the top 6 movies
        result = self.bot.database.execute("SELECT title, media_year, get_movie_score(title, media_year) "
                                           " FROM main.plex_history_messages "
                                           "WHERE media_type = 'movie' GROUP BY title, media_year "
                                           "ORDER BY get_movie_score(title, media_year) DESC LIMIT 6")

        # Format the data into a nice embed
        embed = discord.Embed(title="Top Movies",
                              description="The top 6 movies are:",
                              color=0x00ff00)
        embed.add_field(name="Top Movies",
                        value="\n".join([f"`{i + 1}`. `{movie[0]}({movie[1]})` - Score: `{movie[2]:.2f}`"
                                         for i, movie in enumerate(result)]), inline=False)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PlexStatistics(bot))
    logging.info("PlexStatistics Loaded Successfully")
