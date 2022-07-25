import datetime
import typing

import discord
import humanize as humanize
import plexapi.base
import plexapi.video
from discord.ext import commands
from discord.ext.commands import command
from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction

from utils import cleanup_url


def subtitle_details(content, max_subs=-1) -> list:
    """Get the subtitle details for a media"""
    return_list = []
    file_index = 1
    for media in content.media:
        sub_index = 1
        for part in media.parts:
            file_str = f"`File#{file_index}`: {len(part.subtitleStreams())} subtitles\n"
            for subtitle in part.subtitleStreams():
                opener = "`┠──>" if sub_index < len(part.subtitleStreams()) else "`└──>"
                file_str += f"{opener} {sub_index}[{str(subtitle.codec).upper()}]"\
                            f": {subtitle.language} - {subtitle.title if subtitle.title else 'Unnamed'}"\
                            f"{' - Forced' if subtitle.forced else ''}`\n"
                sub_index += 1
                if max_subs != -1 and sub_index > max_subs:
                    file_str += f"`└──> {len(part.subtitleStreams()) - max_subs} more subs hidden`"
                    break
            return_list.append(file_str)
        file_index += 1

    if len(return_list) == 0:
        return_list.append("No subtitles found")
    return return_list


def get_media_info(media_list: [plexapi.media.Media]) -> list:
    """Get the media info for a list of media"""
    media_info = []
    if len(media_list) == 0:
        return ["`No media found`"]
    else:
        media_index = 1
        for media in media_list:
            for part in media.parts:
                if part.deepAnalysisVersion != 6:
                    # Send a command to the plex sever to run a deep analysis on this part
                    this_media = f"`File#{media_index}`: `{media.videoCodec}:{media.width}x" \
                                 f"{media.height}@{media.videoFrameRate} " \
                                 f"| {media.audioCodec}: {media.audioChannels}ch`\n" \
                                 f"┕──> `Insufficient deep analysis data, L:{part.deepAnalysisVersion}`"

                else:
                    video_stream = part.videoStreams()[0]
                    duration = datetime.timedelta(seconds=round(media.duration / 1000))
                    bitrate = humanize.naturalsize(video_stream.bitrate * 1000)
                    this_media = f"`File#{media_index}`: `{media.videoCodec}:{video_stream.width}x" \
                                 f"{video_stream.height}@{video_stream.frameRate} Bitrate: {bitrate}/s`\n"
                    audio_streams = []
                    stream_num = 1
                    streams = part.audioStreams()
                    for audio_stream in streams:
                        opener = "`┠──>" if stream_num < len(streams) else "`└──>"
                        audio_bitrate = f"{humanize.naturalsize(audio_stream.bitrate * 1000)}/s".rjust(10)
                        audio_streams.append(f"{opener}{audio_bitrate}-{audio_stream.displayTitle}@"
                                             f"{audio_stream.samplingRate / 1000}Khz"
                                             f", Lang: {audio_stream.language}`")
                        stream_num += 1
                    media_index += 1
                    this_media += "\n".join(audio_streams)

                media_info.append(this_media)
    return media_info


def get_season(plex, show_name, season_num):
    for section in plex.library.sections():
        for content in section.search(show_name):
            if content.type == "show":
                for season in content.seasons():
                    if season.index == season_num:
                        return season
    return None


def rating_formatter(rating):
    if rating is None:
        return "N/A"
    else:
        return f"{round(rating * 10)}%"


def stringify(objects: [], separator: str = ", ", max_length: int = -1) -> str:
    """Convert a list of genres to a string"""
    str_objects = []
    if max_length == -1:
        max_length = len(objects)
    for obj in objects[:max_length]:
        if hasattr(obj, "title"):
            str_objects.append(obj.title)
        elif hasattr(obj, "tag"):
            str_objects.append(obj.tag)
        else:
            str_objects.append(str(obj))

    # If there are more than max_length objects, add a +n more to the end
    if len(objects) > max_length:
        str_objects.append(f"+{len(objects) - max_length} more")

    if len(str_objects) == 0:
        return "None"
    return safe_field(separator.join(str_objects))


def safe_field(field_text: str) -> str:
    """Make sure the field text follows all the rules for a field"""
    if field_text is None or field_text == "":
        return "N/A"
    elif len(field_text) > 1024:
        return field_text[:1020] + "..."
    else:
        return field_text


def make_episode_selector(season) -> typing.Union[typing.List[Select], Button] or None:
    """Make an episode selector for a show"""
    if len(season.episodes()) == 0:
        return None
    elif len(season.episodes()) <= 25:
        select_things = [Select(
            custom_id=f"content_search_{hash(season)}",
            placeholder="Select an episode",
            options=[
                SelectOption(
                    label=f"Episode: {result.title}",
                    value=f"e_{result.grandparentTitle}_{result.parentIndex}_{result.index}_{hash(result)}",
                    default=False,
                ) for result in season.episodes()
            ],
        )]
    else:
        # If there are more than 25 episodes, make a selector for every 25 episodes
        split_episodes = [season.episodes()[i: i + 25] for i in range(0, len(season.episodes()), 25)]
        select_things = [
            Select(
                custom_id=f"content_search_{hash(season)}_{i}",
                placeholder=f"Select an episode ({i}/{len(split_episodes)})",
                options=[
                    SelectOption(
                        label=f"Episode: {result.title}",
                        value=f"e_{result.grandparentTitle}_{result.parentIndex}_{result.index}_{hash(result)}",
                        default=False,
                    ) for result in episodes
                ],
            )
            for i, episodes in enumerate(split_episodes)
        ]
    cancel_button = Button(
        label="Cancel",
        style=ButtonStyle.red,
        custom_id=f"cancel_{hash(season)}",
    )
    return select_things + [cancel_button]


def make_season_selector(show) -> typing.Union[typing.List[Select], Button] or None:
    """Make a season selector for a show"""
    if len(show.seasons()) == 0:
        return None
    elif len(show.seasons()) <= 25:
        select_things = [Select(
            custom_id=f"content_search_{hash(show)}",
            placeholder="Select a season",
            options=[
                SelectOption(
                    label=f"Season {result.index}",
                    value=f"s_{result.parentTitle}_{result.index}_{hash(result)}",
                    default=False,
                ) for result in show.seasons()
            ],
        )]
    else:
        # If there are more than 25 seasons, make a selector for every 25 seasons
        split_seasons = [show.seasons()[i: i + 25] for i in range(0, len(show.seasons()), 25)]
        select_things = [
            Select(
                custom_id=f"content_search_{hash(show)}_{i}",
                placeholder=f"Select a season ({i}/{len(split_seasons)})",
                options=[
                    SelectOption(
                        label=f"Season {result.index}",
                        value=f"s_{result.parentTitle}_{result.index}_{hash(result)}",
                        default=False,
                    ) for result in seasons
                ],
            )
            for i, seasons in enumerate(split_seasons)
        ]
    cancel_button = Button(
        label="Cancel",
        style=ButtonStyle.red,
        custom_id=f"cancel_{hash(show)}",
    )
    return select_things + [cancel_button]


class PlexSearch(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.content_cache = {}

    def rating_str(self, content) -> str:
        """Get the rating string for a media"""
        if hasattr(content, 'audienceRating') and hasattr(content, 'rating'):
            rating_string = f"`{content.contentRating}` | " \
                            f"Audience `{rating_formatter(content.audienceRating)}`" \
                            f" | Critics `{rating_formatter(content.rating)}`"
        else:
            rating_string = "No ratings available"
        return rating_string

    def base_info_layer(self, embed, content):
        """Make the base info layer for a media"""

        media_info = get_media_info(content.media)

        rating_string = self.rating_str(content)

        embed.add_field(name="Ratings", value=rating_string, inline=False)
        rounded_duration = round(content.duration / 1000)  # Convert time to seconds and round

        if hasattr(content, 'genres'):
            embed.add_field(name="Genres", value=stringify(content.genres), inline=True)
        else:
            embed.add_field(name="Genres", value="Not applicable", inline=True)

        embed.add_field(name="Runtime", value=f"{datetime.timedelta(seconds=rounded_duration)}", inline=True)
        actors = content.roles
        if len(actors) <= 3:
            embed.add_field(name="Lead Actors", value=stringify(actors, max_length=3), inline=False)
        else:
            embed.add_field(name="Cast", value=stringify(actors, max_length=10), inline=False)

        embed.add_field(name="Producers", value=stringify(content.producers), inline=True)
        embed.add_field(name="Directors", value=stringify(content.directors), inline=True)
        embed.add_field(name="Writers", value=stringify(content.writers), inline=True)
        embed.add_field(name="Media", value=safe_field("\n\n".join(media_info)), inline=False)
        embed.add_field(name="Subtitles",
                        value=safe_field("\n\n".join(subtitle_details(content, max_subs=6))), inline=False)

    @command(name="actor_search", aliases=["actor_info", "as"], brief="Search for an actor")
    async def actor_search(self, ctx, *, query: str):
        plex = ctx.plex
        results = plex.search(query, mediatype="actor")
        if len(results) == 0:
            await ctx.send("No results found")
            return
        await ctx.send("Yep that is an actor, but I'm not going to tell you anything about it.")

    @command(name="episode_search", aliases=["episode", "es"])
    async def episode_search(self, ctx, *, query: str):
        """Search for an episode"""
        plex = ctx.plex
        results = plex.search(query)

        # Remove anything that doesn't have a plexapi.video.Video base class
        results = [r for r in results if isinstance(r, plexapi.video.Video)]

        # Remove anything that isn't an episode
        results = [r for r in results if r.type == "episode"]
        await self.search(ctx, results, query)

    @command(name="content_search", aliases=["cs"])
    async def content_search(self, ctx, *, query: str):
        """
        Searches Plex for a specific content.
        """
        plex = ctx.plex
        results = plex.search(query)

        # Remove anything that doesn't have a plexapi.video.Video base class
        results = [r for r in results if isinstance(r, plexapi.video.Video)]

        # Filter out episodes and seasons
        results = [r for r in results if r.type not in ["season", "episode"]]
        await self.search(ctx, results, query)

    async def search(self, ctx, results, query=None):
        """Display the results of a search"""
        if not results:
            await ctx.send("No results found.")
            return
        elif len(results) == 1:
            msg = await ctx.send("Found 1 result. Showing details...")
            await self.content_details(msg, results[0], ctx.author)
            return
        else:
            select_thing = Select(
                custom_id=f"content_search_{ctx.message.id}",
                placeholder="Select a result",
                options=[
                    SelectOption(
                        label=f"{result.title} ({result.year})",
                        value=f"{result.title}_{result.year}_{hash(result)}",
                        default=False,
                    ) for result in results
                ],
            )
            self.bot.component_manager.add_callback(select_thing, self.on_select)
            embed = discord.Embed(title="Search results for '%s'" % query, color=0x00ff00)
            for result in results:
                embed.add_field(name=f"{result.title} ({result.year})", value=result.summary[:1024], inline=False)
            cancel_button = Button(
                label="Cancel",
                style=ButtonStyle.red,
                custom_id=f"cancel_{ctx.message.id}",
            )

            self.bot.component_manager.add_callback(cancel_button, self.on_select)
            await ctx.send(embed=embed, components=[select_thing, cancel_button])

        # Clear all components

    async def on_select(self, inter: Interaction):
        """Handle the selection of a result"""
        if inter.custom_id.startswith("cancel"):
            await inter.disable_components()
            await inter.message.edit(components=[])
            return
        if inter.custom_id.startswith("content_search"):
            # Get the selected result
            plex = await self.bot.fetch_plex(inter.guild)
            librarys = plex.library.sections()
            if inter.values[0].startswith("s"):
                # Season
                show_name = inter.values[0].split("_")[1]
                season_num = int(inter.values[0].split("_")[2])
                season = get_season(plex, show_name, season_num)
                await inter.disable_components()
                await self.content_details(inter.message, season, inter.author, inter)

            elif inter.values[0].startswith("e"):
                # Episode
                show_name = inter.values[0].split("_")[1]
                season_num = int(inter.values[0].split("_")[2])
                episode_num = int(inter.values[0].split("_")[3])
                episode = get_season(plex, show_name, season_num).episodes()[episode_num - 1]
                await inter.disable_components()
                await self.content_details(inter.message, episode, inter.author, inter)
            else:
                # Run plex search
                name = inter.values[0].split("_")[0]
                try:
                    year = int(inter.values[0].split("_")[1])
                except ValueError:
                    year = None
                results = plex.search(name)
                for result in results:
                    if result.year == year:
                        await inter.disable_components()
                        await self.content_details(inter.message, result, inter.author, inter)
                        return
                await inter.message.edit(content="Error, unable to locate requested content.")

    async def content_details(self, edit_msg, content, requester, inter: Interaction = None):
        """Show details about a content"""
        select_things = None

        if content.isPartialObject():  # For some reason plex likes to not give everything we asked for
            content.reload()  # So if plex is being a jerk, we'll reload the content

        if isinstance(content, plexapi.video.Movie):
            """Format the embed being sent for a movie"""
            embed = discord.Embed(title=f"{content.title} ({content.year})",
                                  description=f"{content.tagline}", color=0x00ff00)
            embed.add_field(name="Summary", value=content.summary, inline=False)

            self.base_info_layer(embed, content)

        elif isinstance(content, plexapi.video.Show):  # ----------------------------------------------------------
            """Format the embed being sent for a show"""

            rating_string = self.rating_str(content)

            embed = discord.Embed(title=f"{content.title}",
                                  description=f"{content.tagline}", color=0x00ff00)
            embed.add_field(name="Rating", value=rating_string, inline=False)
            embed.add_field(name="Genres", value=stringify(content.genres), inline=True)
            embed.add_field(name="Network", value=content.network, inline=True)
            embed.add_field(name="Studio", value=content.studio, inline=True)
            embed.add_field(name="Average Episode Runtime",
                            value=f"{datetime.timedelta(milliseconds=content.duration)}", inline=True)
            embed.add_field(name="Total Seasons", value=content.childCount, inline=True)
            embed.add_field(name="Total Episodes", value=f"{len(content.episodes())}", inline=True)
            # embed.add_field(name="Media", value="\n".join(media_info), inline=False)
            select_things = make_season_selector(content)
            for item in select_things:
                self.bot.component_manager.add_callback(item, self.on_select)

        elif isinstance(content, plexapi.video.Season):  # ------------------------------------------------------
            """Format the embed being sent for a season"""
            embed = discord.Embed(title=f"{content.parentTitle}",
                                  description=f"Season {content.index}", color=0x00ff00)
            embed.add_field(name=f"Episodes: {len(content.episodes())}",
                            value=stringify(content.episodes(), separator="\n")[:1024], inline=False)
            select_things = make_episode_selector(content)
            for item in select_things:
                self.bot.component_manager.add_callback(item, self.on_select)

        elif isinstance(content, plexapi.video.Episode):  # ------------------------------------------------------
            """Format the embed being sent for an episode"""
            embed = discord.Embed(title=f"{content.grandparentTitle}\n{content.title} "
                                        f"(S{content.parentIndex}E{content.index})",
                                  description=f"{content.summary}", color=0x00ff00)
            self.base_info_layer(embed, content)

        else:
            embed = discord.Embed(title="Unknown content type", color=0x00ff00)

        ###############################################################################################################

        if inter is not None:
            await inter.disable_components()

        if hasattr(content, "thumb"):
            thumb_url = cleanup_url(content.thumb)
            embed.set_thumbnail(url=thumb_url)

        # embed.set_footer(text=f"{content.guid}", icon_url=requester.avatar_url)
        embed.set_author(name=f"Requested by: {requester.display_name}", icon_url=requester.avatar_url)
        embed.set_footer(text=f"Located in {content.librarySectionTitle}")
        if select_things:
            await edit_msg.edit(embed=embed, components=select_things)
        else:
            await edit_msg.edit(embed=embed, components=[])

    @command(name="library", aliases=["lib", "libraries"], description="List all libraries")
    async def library_list(self, ctx):
        pass


def setup(bot):
    bot.add_cog(PlexSearch(bot))
    print(f"Loaded {__name__}")
