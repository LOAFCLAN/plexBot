import asyncio
import datetime
import random

import discord
import humanize
import plexapi.video
from discord.ext import commands

from wrappers_utils.SessionChangeWatchers import SessionWatcher, SessionChangeWatcher
from utils import base_info_layer, get_season, get_episode, cleanup_url, text_progress_bar_maker, stringify, \
    base_user_layer, get_all_library, get_watch_time, get_session_count

from loguru import logger as logging


class PlexStatistics(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.history_table = self.bot.database.get_table("plex_history_messages")

    @commands.hybrid_command(name="library_stats")
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

            def get_library():
                content = library.all()
                media_length = round(library.totalDuration / 1000)
                top_media_count = library.totalSize
                # Because TV shows only count as one item, we need to get the total number of episodes for
                # the total number of episodes
                media_count = 0
                for item in content:
                    if isinstance(item, plexapi.video.Show):
                        media_count += len(item.episodes())
                    else:
                        media_count += 1
                media_size = library.totalStorage
                return content, media_length, top_media_count, media_count, media_size

            library_content, total_media_length, top_level_media_count, total_media_count, total_media_size \
                = await self.bot.loop.run_in_executor(None, get_library)  # Run in executor to prevent blocking

            # Get the total watch time for the library
            watch_time = self.bot.database.get("SELECT SUM(watch_time) FROM plex_history_events WHERE media_id IN "
                                               f"(SELECT media_id FROM plex_watched_media WHERE"
                                               f" library_id = {library.key})")[0][0]
            session_count = self.bot.database.get("SELECT COUNT(*) FROM plex_history_events WHERE media_id IN "
                                                  f"(SELECT media_id FROM plex_watched_media WHERE"
                                                  f" library_id = {library.key})")[0][0]

            if watch_time is None:
                watch_time = 0

            embed = discord.Embed(title=f"Library Statistics for {library.title}",
                                  description=f"Media Length: "
                                              f"`{datetime.timedelta(seconds=total_media_length)}`\n"
                                              f"Media Watch Time: `"
                                              f"{datetime.timedelta(seconds=round(watch_time / 1000))}`\n"
                                              f"Session Count: `{session_count}`\n"
                                              f"Media Count: `{total_media_count} | {top_level_media_count}`\n"
                                              f"Media Size: `{humanize.naturalsize(total_media_size)}`",
                                  color=0x00ff00)
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

            embed.add_field(name="Top Media Elements",
                            value="\n".join([f"`{str(i + 1).zfill(2)}. "
                                             f"{str(round(media[2] / media[3] * 100)).zfill(3)}%` - "
                                             f"`{datetime.timedelta(seconds=media[2])}` - "
                                             f"`{media[0][:25]}`"
                                             for i, media in enumerate(most_popular)]), inline=False)
            await ctx.send(embed=embed)

    @commands.hybrid_group(name="user_stats", aliases=["user_statistics", "us"], invoke_without_command=True)
    async def user_stats(self, ctx):
        pass

    @user_stats.command(name="watch_percentage", aliases=["watch_percent", "watch_percentages"])
    async def watch_percentage(self, ctx, *, user_info):
        """
        Gets the media the user has watched sorted by percentage of total watch time.
        """
        # Send a typing indicator
        async with ctx.typing():
            # Check if user_info is a user mention
            if ctx.message.mentions:
                user_info = ctx.message.mentions[0]
            # Get the user
            user = ctx.plex.associations.get(user_info)  # type: wrappers_utils.CombinedUser
            if user is None:
                await ctx.send("Could not find a CombinedUser matching that name.")
                return
            # print(user)
            # Get the user's watch history
            # print(user.account_id)
            watch_history_movies = self.bot.database.get(f"""
            SELECT media.title, media.media_guid,
            SUM(events.watch_time) / 1000 AS total_watch_time,
            media.media_length AS length
            FROM plex_watched_media AS media
            JOIN plex_history_events AS events ON events.media_id = media.media_id
            WHERE events.account_id = ? AND media.media_type = 'movie'
            GROUP BY media.media_id ORDER BY (total_watch_time * 1000 / length) DESC LIMIT 15;""", (user.account_id,))

            watch_history_shows = self.bot.database.get(f"""
            SELECT show.title, show.media_guid,
            SUM(DISTINCT events.watch_time) / 1000 AS total_watch_time, 
            show.media_length AS length,
            COUNT(DISTINCT media.title) AS total_episodes
            FROM plex_watched_media AS media
            JOIN plex_history_events AS events ON events.media_id = media.media_id
            JOIN plex_watched_media
                AS show ON show.media_type = 'show'
            WHERE events.account_id = ? AND media.media_type = 'episode' and show.media_id = media.show_id
            GROUP BY show.media_id
            ORDER BY (total_watch_time * 1000 / length) DESC LIMIT 15;""", (user.account_id,))

            watch_history = watch_history_movies + watch_history_shows
            # Resorted by percentage of total watch time
            watch_history.sort(key=lambda x: x[2] / x[3], reverse=True)
            # print(watch_history)
            watch_history = watch_history[:15]

            server_sessions = self.bot.database.get("""SELECT COUNT(*) FROM plex_history_events""")[0][0]
            session_percentage = round((len(user.sessions) / server_sessions) * 100, 2)

            server_watch_time = self.bot.database.get("""SELECT SUM(watch_time) FROM plex_history_events""")[0][0]
            user_watch_time = self.bot.database.get(
                """SELECT SUM(watch_time) FROM plex_history_events WHERE account_id = ?""", (user.account_id,))[0][0]
            watch_time_percentage = round((user_watch_time / server_watch_time) * 100, 2)

            embed = discord.Embed(title=f"Watch Percentages for {user.display_name()}",
                                  description=f"Total Watch Time: "
                                              f"`{datetime.timedelta(seconds=round(user.total_watch_time / 1000))} | "
                                              f"{watch_time_percentage}%`\n"
                                              f"Session Count: `{len(user.sessions)} | {session_percentage}%`\n"
                                              f"Media Count: `{user.unique_media_count}`", color=0x00ff00)

            embed.add_field(name="Top Media Elements",
                            value="\n".join([f"`{str(i + 1).zfill(2)}. "
                                             f"{str(round(media[2] / media[3] * 100)).zfill(3)}%` - "
                                             f"`{datetime.timedelta(seconds=media[2])}` - "
                                             f"`{media[0][:25]}`"
                                             for i, media in enumerate(watch_history)]), inline=False)

            # Make sure each field is under 1024 characters
            for field in embed.fields:
                if len(field.value) > 1024:
                    embed.set_field_at(embed.fields.index(field), name=field.name, value=field.value[:1024])

            await ctx.send(embed=embed)

    @commands.hybrid_command(name="who_watched", aliases=["watched_by", "watched", "ww", "wb"])
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
