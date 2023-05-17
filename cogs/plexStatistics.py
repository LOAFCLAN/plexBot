import asyncio
import datetime
import random

import discord
import humanize
import plexapi.video
from ConcurrentDatabase.DynamicEntry import DynamicEntry
from discord import Interaction, ButtonStyle, ActionRow
from discord.ext import commands
from discord.ext.commands import Cog, command, has_permissions
# import custom_dpy_overrides
# from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction, ActionRow
from discord.ui import Button, View, Select

from wrappers_utils.SessionChangeWatchers import SessionWatcher, SessionChangeWatcher
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
                most_popular = self.bot.database.get("""
                SELECT show.title, show.media_guid,
                SUM(DISTINCT events.watch_time) / 1000 AS total_watch_time,
                show.media_length AS length,
                COUNT(DISTINCT media.title) AS total_episodes
                FROM plex_watched_media AS media
                JOIN plex_history_events AS events ON events.media_id = media.media_id
                JOIN plex_watched_media
                    AS show ON show.media_type = 'show'
                WHERE media.media_type = 'episode' and show.media_id = media.show_id AND show.library_id = ?
                GROUP BY show.media_id
                ORDER BY (total_watch_time * 1000 / length) DESC LIMIT 15""", (library.key,))
            else:
                most_popular = self.bot.database.get("""
                SELECT media.title, media.media_guid,
                SUM(events.watch_time) / 1000 AS total_watch_time,
                media.media_length AS length
                FROM plex_watched_media AS media
                JOIN plex_history_events AS events ON events.media_id = media.media_id
                WHERE media.media_type = 'movie' AND media.library_id = ?
                GROUP BY media.media_id
                ORDER BY (total_watch_time * 1000 / length) DESC LIMIT 15;""", (library.key,))

            # print(most_popular)

            embed.set_field_at(0, name="Top Media Elements",
                               value="\n".join([f"`{str(i + 1).zfill(2)}. "
                                                f"{round(media[2] / media[3] * 100)}%` - "
                                                f"`{datetime.timedelta(seconds=media[2])}` - "
                                                f"`{media[0][:25]}`"
                                                for i, media in enumerate(most_popular)]), inline=False)
            await message.edit(embed=embed)

    @commands.command(name="who_watched", aliases=["watched_by", "watched", "ww", "wb"])
    async def who_watched(self, ctx, *, media_name):
        # Preform a search for the media
        async with ctx.typing():
            search_results = ctx.plex.search(media_name)
            # Get the media object
            search_results = [r for r in search_results if isinstance(r, plexapi.video.Video)]

            # Filter out episodes and seasons
            search_results = [r for r in search_results if r.type not in ["season", "episode"]]

            embed = discord.Embed(title=f"Who Watched",
                                  color=0x00ff00)
            for plex_media in search_results:
                # Get the media history
                media = self.bot.database.get_table("plex_watched_media").get_row(media_guid=plex_media.guid)
                if media is None:
                    embed.add_field(name=f"Who Watched \"{plex_media.title}\" ({plex_media.year})",
                                    value="No one has watched this media yet.", inline=False)
                    continue
                if media["media_type"] == "show":
                    results = self.bot.database.get("""
                    SELECT events.account_id,
                        SUM(events.watch_time) / 1000 AS total_watch_time,
                        COUNT(events.watch_time) AS total_watches
                    FROM plex_history_events AS events
                    JOIN plex_watched_media AS media ON media.media_id = events.media_id
                    WHERE media.media_type = 'episode' AND media.show_id = ?
                    GROUP BY events.account_id ORDER BY total_watch_time DESC""", (media["media_id"],))

                else:
                    results = self.bot.database.get("""
                    SELECT events.account_id,
                        SUM(events.watch_time) / 1000 AS total_watch_time,
                        COUNT(events.watch_time) AS total_watches
                    FROM plex_history_events AS events
                    JOIN plex_watched_media AS media ON media.media_id = events.media_id
                    WHERE media.media_type = 'movie' AND media.media_id = ?
                    GROUP BY events.account_id ORDER BY total_watch_time DESC""", (media["media_id"],))

                if len(results) == 0:
                    embed.description = "No one has watched this media."
                    await ctx.send(embed=embed)
                    return

                # Get the users who watched the media (remove duplicates)
                # Get the user objects
                users = [(ctx.plex.associations.get(account_id), total_watch_time, total_watches) for
                         account_id, total_watch_time, total_watches in results]
                # Add the user names to the embed
                embed.add_field(name=f"Who Watched \"{plex_media.title}\" ({plex_media.year})",
                                value="\n".join([f"`{i + 1}.` {user.mention()} - "
                                                 f"`{datetime.timedelta(seconds=total_watch_time)}`"
                                                 for i, (user, total_watch_time, total_watches) in enumerate(users)])
                                , inline=False)
            await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PlexStatistics(bot))
    logging.info("PlexStatistics Loaded Successfully")
