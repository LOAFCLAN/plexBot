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

            embed = discord.Embed(title=f"Library Statistics for {library.title}",
                                  description=f"Total Media Length: "
                                              f"`{datetime.timedelta(seconds=total_media_length)}`\n"
                                              f"Total Media Watch Time: Loading...\n"
                                              f"Total Session Count: Loading...\n"
                                              f"Total Media Count: `{total_media_count} | {top_level_media_count}`\n"
                                              f"Total Media Size: `{humanize.naturalsize(total_media_size)}`",
                                  color=0x00ff00)
            embed.add_field(name="Detailed Statistics", value="Loading...", inline=False)
        message = await ctx.send(embed=embed)

        # For each item in the library get its info from the database
        library_content_info = []
        for item in library_content:
            watch_time = get_watch_time(item, self.bot.database)
            session_count = get_session_count(item, self.bot.database)
            library_content_info.append((item, watch_time, session_count))

        # Add all the timedeltas together to get the total watch time
        total_watch_time = datetime.timedelta(seconds=0)
        total_session_count = 0
        for item, watch_time, session_count in library_content_info:
            total_watch_time += watch_time
            total_session_count += session_count

        # Sort the library content by watch time
        library_content_info.sort(key=lambda x: x[1], reverse=True)

        # Add the information to the embed
        embed.description = f"Total Media Length: `{datetime.timedelta(seconds=total_media_length)}`\n" \
                            f"Total Media Watch Time: `{total_watch_time}`\n" \
                            f"Total Session Count: `{total_session_count}`\n" \
                            f"Total Media Count: `{total_media_count} | {top_level_media_count}`\n" \
                            f"Total Media Size: `{humanize.naturalsize(total_media_size)}`"
        embed.set_field_at(0, name="Top Media Elements",  # Max 10 entries
                           value="\n".join([f"`{i + 1}`. `{item[0].title}` - "
                                            f"{item[1]}"
                                            for i, item in enumerate(library_content_info[:10])]), inline=False)
        await message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(PlexStatistics(bot))
    logging.info("PlexStatistics Loaded Successfully")
