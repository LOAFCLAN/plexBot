import asyncio
import datetime
import random

import discord
import humanize
import plexapi.video
from discord import Interaction, ButtonStyle, ActionRow
from discord.ext import commands
from discord.ext.commands import Cog, command, has_permissions
# import custom_dpy_overrides
# from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction, ActionRow
from discord.ui import Button, View, Select

from plex_wrappers import SessionChangeWatcher, SessionWatcher
from utils import base_info_layer, get_season, get_episode, cleanup_url, text_progress_bar_maker, stringify, \
    base_user_layer, get_all_library, get_watch_time, get_session_count

from loguru import logger as logging


class PlexStatistics(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.history_table = self.bot.database.get_table("plex_history_messages")

    @commands.command(name="global_stats")
    async def global_stats(self, ctx):
        """Gets global watch statistics for the server, such as total watch time, total number of episodes watched,
           most popular movie, show etc."""
        total_watch_time = self.bot.database.get("SELECT SUM(watch_time) FROM plex_history_events")[0][0]
        total_sessions = self.bot.database.get("SELECT COUNT(*) FROM plex_history_events")[0][0]

        # The information we need to determine the media elements with the highest watch percentage are
        # split across two tables, so we need use a subquery to get the information we need.

        def calc_watch_percentage(media_id):
            result = self.bot.database.execute(
                f"SELECT media_length FROM plex_watched_media WHERE media_id = {media_id}")
            media_duration = result.fetchone()[0]
            result = self.bot.database.execute(
                f"SELECT SUM(watch_time) FROM plex_history_events WHERE media_id = {media_id}")
            media_watch_time = result.fetchone()[0]
            if media_duration == 0 or media_duration is None or media_watch_time is None:
                return 0
            else:
                return (media_watch_time / 1000) / media_duration

        self.bot.database.create_function("calc_watch_percentage", 1, calc_watch_percentage)

        most_popular_movies = self.bot.database.get(
            "SELECT media_id, title, calc_watch_percentage(media_id) "
            "FROM plex_watched_media WHERE media_type = 'movie' ORDER BY calc_watch_percentage(media_id) DESC LIMIT 9")

        # Format the data into a nice embed
        embed = discord.Embed(title="Global Plex Statistics",
                              description=f"Globally, `{total_sessions}` sessions have been logged,"
                                          f" totalling `{datetime.timedelta(seconds=round(total_watch_time / 1000))}`"
                                          f" of watch time.",
                              color=0x00ff00)
        embed.add_field(name="Most Popular Movies by Watch Percentage",
                        value="\n".join([f"`{i + 1}`. `{movie[1]}` - {round(movie[2] * 100)}%"
                                         for i, movie in enumerate(most_popular_movies)]), inline=False)

        # embed.add_field(name="Most Popular Shows by Watch Percentage",
        #                 value="\n".join([f"`{i + 1}`. `{show[0]}` - {datetime.timedelta(seconds=round(show[1]))}"
        #                                  for i, show in enumerate(most_popular_shows)]), inline=False)
        #
        # embed.add_field(name="Most Popular Episodes by Watch Percentage",
        #                 value="\n".join([f"`{i + 1}`. `{episode[0]} - S{episode[1]}E{episode[2]}` - "
        #                                  f"{datetime.timedelta(seconds=round(episode[3]))}"
        #                                  for i, episode in enumerate(most_popular_episodes)]), inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="library_stats")
    async def library_stats(self, ctx, *, library_name):
        """
        Gets library watch statistics for the server, such as total watch time, total number of episodes watched,
        most popular content from that library.
        """

        # Send a typing indicator
        async with ctx.typing():
            libraries = get_all_library(ctx.plex)
            for library in libraries:
                if library.title.lower() == library_name.lower():
                    library = library
                    break
            else:
                await ctx.send("Could not find library with that name.")
                return

            library_content = library.all()
            total_media_length = round(library.totalDuration / 1000)
            top_level_media_count = library.totalSize
            # Because TV shows only count as one item, we need to get the total number of episodes for
            # the total number of episodes
            total_media_count = 0
            for item in library_content:
                if isinstance(item, plexapi.video.Show):
                    total_media_count += len(item.episodes())
                else:
                    total_media_count += 1
            total_media_size = library.totalStorage

            # Get the total watch time for the library
            watch_time = self.bot.database.get("SELECT SUM(watch_time) FROM plex_history_events WHERE media_id IN "
                                               f"(SELECT media_id FROM plex_watched_media WHERE"
                                               f" library_id = {library.key})")[0][0]
            session_count = self.bot.database.get("SELECT COUNT(*) FROM plex_history_events WHERE media_id IN "
                                                  f"(SELECT media_id FROM plex_watched_media WHERE"
                                                  f" library_id = {library.key})")[0][0]

            embed = discord.Embed(title=f"Library Statistics for {library.title}",
                                  description=f"Total Media Length: "
                                              f"`{datetime.timedelta(seconds=total_media_length)}`\n"
                                              f"Total Media Watch Time: `"
                                              f"{datetime.timedelta(seconds=round(watch_time / 1000))}`\n"
                                              f"Total Session Count: `{session_count}`\n"
                                              f"Total Media Count: `{total_media_count} | {top_level_media_count}`\n"
                                              f"Total Media Size: `{humanize.naturalsize(total_media_size)}`",
                                  color=0x00ff00)
            embed.add_field(name="Top Media Elements", value="Loading...", inline=False)
            message = await ctx.send(embed=embed)
            if library.type == "show":
                most_popular = self.bot.database.get(
                    f"SELECT show.title, show.media_guid, "
                    f"SUM(events.watch_time) / show.media_length, "
                    f"SUM(events.watch_time) "
                    f"FROM plex_history_events AS events "
                    f"INNER JOIN plex_watched_media AS media ON events.media_id = show.media_id "
                    f"INNER JOIN plex_watched_media AS show ON media.show_id = show.media_id " 
                    f"WHERE show.library_id = {library.key} and show.media_type = 'show' "
                    f"GROUP BY show.media_id "
                    f"ORDER BY SUM(events.watch_time) DESC LIMIT 9")
            else:
                most_popular = self.bot.database.get(
                    f"SELECT media.title, media.media_guid, "
                    f"SUM(events.watch_time) / media.media_length as watch_percentage,"
                    f"SUM(events.watch_time) as watch_time "
                    f"FROM plex_watched_media as media "
                    f"INNER JOIN plex_history_events as events ON media.media_id = events.media_id "
                    f"WHERE media.library_id = {library.key} AND media.media_length != 0 "
                    f"GROUP BY media.title, media.media_year "
                    f"ORDER BY watch_percentage DESC LIMIT 9")

            print(most_popular)

            embed.set_field_at(0, name="Top Media Elements",
                               value="\n".join([f"`{i + 1}`. `{movie[0]}` - {round(movie[2] / 10)}% - "
                                                f"`{datetime.timedelta(seconds=round(movie[3] / 1000))}`"
                                                for i, movie in enumerate(most_popular)]), inline=False)
            await message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(PlexStatistics(bot))
    logging.info("PlexStatistics Loaded Successfully")
