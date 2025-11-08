import datetime
import re
import traceback

import langcodes
import humanize
import plexapi
import typing
# from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction
from discord import ButtonStyle
from discord.ui import Select, Button, View
import discord

__all__ = ['clean', 'is_clean', 'get_season', 'base_info_layer', 'rating_str', 'stringify', 'make_season_selector',
           'make_episode_selector', 'cleanup_url', 'get_episode', 'text_progress_bar_maker']

from discord.ui import Select

from wrappers_utils.CombinedUser import CombinedUser

from loguru import logger as logging

mass_mention = re.compile('@(everyone|here)')
member_mention = re.compile(r'<@\!?(\d+)>')
role_mention = re.compile(r'<@&(\d+)>')
channel_mention = re.compile(r'<#(\d+)>')


def clean(ctx, text=None, *, mass=True, member=True, role=True, channel=True):
    """Cleans the message of anything specified in the parameters passed."""
    if text is None:
        text = ctx.message.content
    cleaned_text = text
    if mass:
        cleaned_text = mass_mention.sub(lambda match: '@\N{ZERO WIDTH SPACE}' + match.group(1), cleaned_text)
    if member:
        cleaned_text = member_mention.sub(lambda match: clean_member_name(ctx, int(match.group(1))), cleaned_text)
    if role:
        cleaned_text = role_mention.sub(lambda match: clean_role_name(ctx, int(match.group(1))), cleaned_text)
    if channel:
        cleaned_text = channel_mention.sub(lambda match: clean_channel_name(ctx, int(match.group(1))), cleaned_text)
    return cleaned_text


def cleanup_url(url):
    return f"https://celery.loafclan.org/plex-image-links-and-stuff---yeah{url}.jpg"


def is_clean(ctx, text=None):
    """Checks if the message is clean already and doesn't need to be cleaned."""
    if text is None:
        text = ctx.message.content
    return all(regex.search(text) is None for regex in (mass_mention, member_mention, role_mention, channel_mention))


def clean_member_name(ctx, member_id):
    """Cleans a member's name from the message."""
    member = ctx.guild.get_member(member_id)
    if member is None:
        return '<@\N{ZERO WIDTH SPACE}%d>' % member_id
    elif is_clean(ctx, member.display_name):
        return member.display_name
    elif is_clean(ctx, str(member)):
        return str(member)
    else:
        return '<@\N{ZERO WIDTH SPACE}%d>' % member.id


def clean_role_name(ctx, role_id):
    """Cleans role pings from messages."""
    role = discord.utils.get(ctx.guild.roles, id=role_id)  # Guild.get_role doesn't exist
    if role is None:
        return '<@&\N{ZERO WIDTH SPACE}%d>' % role_id
    elif is_clean(ctx, role.name):
        return '@' + role.name
    else:
        return '<@&\N{ZERO WIDTH SPACE}%d>' % role.id


def clean_channel_name(ctx, channel_id):
    """Cleans channel mentions from messages."""
    channel = ctx.guild.get_channel(channel_id)
    if channel is None:
        return '<#\N{ZERO WIDTH SPACE}%d>' % channel_id
    elif is_clean(ctx, channel.name):
        return '#' + channel.name
    else:
        return '<#\N{ZERO WIDTH SPACE}%d>' % channel.id


def get_all_library(plex) -> typing.List[plexapi.library.LibrarySection]:
    all_library = []
    for library in plex.library.sections():
        all_library.append(library)
    return all_library


def pretty_concat(strings, single_suffix='', multi_suffix=''):
    """Concatenates things in a pretty way"""
    if len(strings) == 1:
        return strings[0] + single_suffix
    elif len(strings) == 2:
        return '{} and {}{}'.format(*strings, multi_suffix)
    else:
        return '{}, and {}{}'.format(', '.join(strings[:-1]), strings[-1], multi_suffix)


def translate(lang):
    if lang is None:
        return "Unknown"
    try:
        return langcodes.find(lang).display_name()
    except Exception as e:
        # print(f"Translation error: {e}\n{traceback.format_exc()}")
        return f"{lang}*"


async def session_embed(plex):
    plex_sessions = plex.sessions()

    if not plex.online:
        embed = discord.Embed(title="Server Offline",
                              description="The target Plex Media Server is offline.",
                              color=discord.Color.red(), timestamp=datetime.datetime.now())
        return embed

    if len(plex_sessions) == 0:
        embed = discord.Embed(title="Plex Sessions",
                              description="There are currently no sessions in progress", color=0x00ff00)
    elif len(plex_sessions) == 1:
        embed = discord.Embed(title="Plex Sessions",
                              description="There is currently **1** session in progress", color=0x00ff00)
    else:
        embed = discord.Embed(title="Plex Sessions",
                              description=f"There are currently **{len(plex_sessions)}** sessions in progress",
                              color=0x00ff00)

    total_bandwidth = 0
    # available_bandwidth = -1

    for session in plex_sessions:
        try:
            total_bandwidth += make_session_entry(plex, session, embed)
        except Exception as e:
            if hasattr(session, 'title') and hasattr(session, 'usernames'):
                if len(session.usernames) > 0:
                    embed.add_field(name=f"Error with {session.title} ({session.usernames[0]})",
                                    value=f"```{e}```", inline=False)
                else:
                    embed.add_field(name=f"Error with {session.title}",
                                    value=f"```{e}```", inline=False)
            else:
                embed.add_field(name=f"Error with session",
                                value=f"```{e}```", inline=False)
            logging.error(f"Error in session embed: {e}\n{traceback.format_exc()}")

    embed.timestamp = datetime.datetime.now()
    embed.set_footer(text=f"{round(total_bandwidth)} kps of bandwidth reserved")
    return embed


def get_stream_parts(media: plexapi.media.Media) -> typing.Tuple[plexapi.media.VideoStream, plexapi.media.AudioStream,
                                                                 plexapi.media.SubtitleStream]:
    video_stream = None
    audio_stream = None
    subtitle_stream = None

    parts = getattr(media, 'parts', [])
    for part in parts:
        for stream in getattr(part, 'streams', []):
            if stream.STREAMTYPE == 1:
                video_stream = stream
            elif stream.STREAMTYPE == 2:
                audio_stream = stream
            elif stream.STREAMTYPE == 3:
                subtitle_stream = stream

    return video_stream, audio_stream, subtitle_stream


def make_session_entry(plex, session, embed):
    if not session.isFullObject:
        session.reload(checkFiles=False)
        if not session.isFullObject:
            embed.add_field(name="Session Error",
                            value=f"Session `{session.id}` is not a full object and could not be reloaded")
            return

    if isinstance(session.session, list):
        session_instance = session.session[0]
    elif isinstance(session.session, plexapi.media.Session):
        session_instance = session.session
    elif isinstance(session, plexapi.video.MovieSession) or isinstance(session, plexapi.video.EpisodeSession):
        session_instance = session
    else:
        if len(session.usernames) == 0:
            embed.add_field(name=f"{session.title}", value="Session has no users", inline=False)
        else:
            embed.add_field(name=f"{session.usernames[0]} has encountered an error",
                            value=f"Invalid session type, {type(session)}", inline=False)
        return

    if len(session.media) > 1:
        # Find which media file has a bitrate closest to the reserved bitrate
        reserved_bitrate = getattr(session_instance, 'bandwidth', 0)
        closest_bitrate = 0
        closest_media = None
        for media in session.media:
            if closest_bitrate < media.bitrate < reserved_bitrate:
                closest_bitrate = media.bitrate
                closest_media = media
        media = closest_media
        if closest_media is None:
            media = session.media[0]
    elif len(session.media) == 1:
        media = session.media[0]
    else:
        media = None

    player = getattr(session, 'player', None)

    video_stream, audio_stream, subtitle_stream = get_stream_parts(media)

    if session.players[0].title:
        device = session.players[0].title
    elif session.players[0].model:
        device = session.players[0].model
    else:
        device = session.players[0].platform

    current_position = datetime.timedelta(seconds=round(session.viewOffset / 1000))
    total_duration = datetime.timedelta(seconds=round(session.duration / 1000))

    progress_bar = text_progress_bar_maker(duration=total_duration.total_seconds(),
                                           end=current_position.total_seconds(), length=35)

    timeline = f"{current_position} / {total_duration} - {str(session.players[0].state).capitalize()}"

    raw_bandwidth = 0
    if hasattr(session_instance, "location"):
        if session_instance.location.startswith("lan"):
            bandwidth = "Local session, no bandwidth reserved"
        else:
            if media is None:
                bandwidth = "Unknown media"
            else:
                bandwidth = f"Reserved `{media.bitrate} kps " \
                            f"{'[RELAY]' if player.relayed else '[DIRECT]'}`"
                raw_bandwidth = media.bitrate
    else:
        bandwidth = "No bandwidth attribute!"

    if len(session.transcodeSessions) == 0:
        media_info = f"`{media.container}` - `{media.videoCodec}:" \
                     f" {media.width}x{media.height}@{media.videoFrameRate} " \
                     f"| {media.audioCodec}: {media.audioChannels}ch`"
    elif len(session.transcodeSessions) == 1:
        transcode = session.transcodeSessions[0]
        media_info = f"`{media.container}` - `{media.videoCodec}:" \
                     f" {media.width}x{media.height}@{media.videoFrameRate} " \
                     f"| {media.audioCodec}: {media.audioChannels}ch`\n"
        transcode_speed = f"{round(transcode.speed, 2)}x" if transcode.speed > 0 else "IDLE"
        media_info += f"`{transcode.sourceVideoCodec}:{transcode.sourceAudioCodec}" \
                      f"`->`{transcode.videoCodec}:{transcode.audioCodec} " \
                      f" [{str(transcode.transcodeHwEncoding).upper()}:{transcode_speed}]`"
    else:
        media_info = "`Multiple transcode sessions detected!`"

    if subtitle_stream is not None:
        name = subtitle_stream.title if subtitle_stream.title else subtitle_stream.language
        media_info += f"\n└──> `{str(subtitle_stream.codec).upper().rjust(4)}:" \
                      f" {name if name else 'Unknown language'}`"

    # print(session.__dict__)
    # print(session.session[0].__dict__)

    if session.type == 'movie':
        value = f"{session.title[:40]} ({session.year})\n" \
                f"{timeline}\n" \
                f"{progress_bar}\n" \
                f"{bandwidth}\n" \
                f"{media_info}"
    elif session.type == 'episode':
        value = f"{session.grandparentTitle[:30]} - `{session.parentTitle}`\n" \
                f"{session.title[:30]} - `Episode {session.index}`\n" \
                f"{timeline}\n" \
                f"{progress_bar}\n" \
                f"{bandwidth}\n" \
                f"{media_info}"
    else:
        value = f"{session.title} - {session.type}\n" \
                f"{timeline}\n" \
                f"{progress_bar}\n" \
                f"{bandwidth}\n" \
                f"{media_info}"
    # print(session.players[0].__dict__)
    try:
        embed.add_field(name=f"{plex.associations.display_name(session.usernames[0])} on {device}", value=value,
                        inline=False)
    except Exception as e:
        embed.add_field(name=f"{session.usernames[0]} on {device} ({type(e)})", value=value, inline=False)

    return raw_bandwidth


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
                if subtitle.title:
                    title = "" if subtitle.title == translate(subtitle.language) else f" - {subtitle.title}"
                else:
                    title = ""
                file_str += f"{opener} {sub_index}[{str(subtitle.codec).upper()}]" \
                            f": {translate(subtitle.language)}{title}" \
                            f"{' - Forced' if subtitle.forced else ''}`\n"
                if max_subs != -1 and max_subs <= sub_index < len(part.subtitleStreams()):
                    file_str += f"`└──> {len(part.subtitleStreams()) - max_subs} more subs hidden`"
                    break
                sub_index += 1
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
                # if part.deepAnalysisVersion != 6:
                #     # Send a command to the plex sever to run a deep analysis on this part
                #     this_media = f"`File#{media_index}`: `{media.videoCodec}:{media.width}x" \
                #                  f"{media.height}@{media.videoFrameRate} " \
                #                  f"| {media.audioCodec}: {media.audioChannels}ch`\n" \
                #                  f"┕──> `Insufficient deep analysis data, L:{part.deepAnalysisVersion}`"
                #
                # else:
                if len(part.videoStreams()) == 0:
                    this_media = f"`File#{media_index}`: `{media.videoCodec}:{media.width}x" \
                                 f"{media.height}@{media.videoFrameRate} " \
                                 f"| {media.audioCodec}: {media.audioChannels}ch`\n" \
                                 f"┕──> `No video streams found!`"
                else:
                    video_stream = part.videoStreams()[0]
                    duration = datetime.timedelta(seconds=round(media.duration / 1000))
                    bitrate = humanize.naturalsize(video_stream.bitrate * 1000)
                    bitrate = f"{bitrate.split(' ')[0]} {bitrate.split(' ')[1].capitalize()}"
                    this_media = f"`File#{media_index}`: `{media.videoCodec}:{video_stream.width}x" \
                                 f"{video_stream.height}@{video_stream.frameRate} Bitrate: {bitrate}/s`\n"
                audio_streams = []
                stream_num = 1
                streams = part.audioStreams()

                required_rjust = 0
                stream_infos = []

                for audio_stream in streams:
                    if audio_stream.codec is None:
                        audio_codec = "IDFK"
                    else:
                        audio_codec = audio_stream.codec.upper()
                    if audio_stream.audioChannelLayout is None:
                        audio_channel_layout = f"{audio_stream.channels} ch"
                    else:
                        audio_channel_layout = audio_stream.audioChannelLayout.capitalize()
                    stream_infos.append(f"{audio_codec}[{audio_channel_layout}]")

                for info in stream_infos:
                    if len(info) > required_rjust:
                        required_rjust = len(info)

                for audio_stream in streams:
                    opener = "`┠──>" if stream_num < len(streams) else "`└──>"
                    if audio_stream.bitrate is None:
                        audio_bitrate = "IDFK"
                    else:
                        bitrate = humanize.naturalsize(audio_stream.bitrate * 1000)
                        audio_bitrate = f"{bitrate.split(' ')[0]} {bitrate.split(' ')[1].capitalize()}/s".rjust(10)
                    if audio_stream.codec is None:
                        audio_codec = "IDFK"
                    else:
                        audio_codec = audio_stream.codec.upper()
                    if audio_stream.audioChannelLayout is None:
                        audio_channel_layout = f"{audio_stream.channels} ch"
                    else:
                        audio_channel_layout = audio_stream.audioChannelLayout.capitalize()
                    stream_info = f"{audio_codec}[{audio_channel_layout}]".rjust(required_rjust)
                    audio_streams.append(f"{opener}{audio_bitrate}-{stream_info}@"
                                         f"{audio_stream.samplingRate / 1000}Khz"
                                         f", Lang: {translate(audio_stream.language)}`")
                    stream_num += 1
                media_index += 1
                this_media += "\n".join(audio_streams)

                media_info.append(this_media)
    return media_info


def get_from_guid(library, guid):
    try:
        return library.getGuid(guid)
    except plexapi.exceptions.NotFound:
        logging.warning(f"Could not find {guid} in {library.title} using getGuid")
        library_content = library.all()
        for content in library_content:
            if content.guid == guid:
                return content
        logging.warning(f"Could not find {guid} in {library.title} using all()")
        return None


def get_from_media_index(library: plexapi.library.LibrarySection,
                         media_index, recent=False) -> typing.Union[plexapi.video.Movie,
                                                                   plexapi.video.Show,
                                                                   plexapi.video.Season,
                                                                   plexapi.video.Episode,
                                                                   None]:
    try:

        def search(search_content):
            for content in search_content:
                if isinstance(content, plexapi.video.Movie):
                    if content.ratingKey == int(media_index):
                        return content
                elif isinstance(content, plexapi.video.Show):
                    if content.ratingKey == int(media_index):
                        return content
                    for season in content.seasons():
                        if season.ratingKey == int(media_index):
                            return season
                        for episode in season.episodes():
                            if episode.ratingKey == int(media_index):
                                return episode
            return None

        if recent:
            recent_content = library.recentlyAdded(maxresults=50).__reversed__()
            found = search(recent_content)
            if found is not None:
                return found
        logging.debug(f"Getting media from index {media_index}")
        all_content = library.all()
        found = search(all_content)
        if found is not None:
            return found
        return None
    except plexapi.exceptions.NotFound:
        logging.warning(f"Could not find {media_index} in {library.title} using all()")
        return None


def get_show(library, show_name):
    try:
        return library.get(show_name)
    except plexapi.exceptions.NotFound:
        logging.warning(f"Could not find {show_name} in {library.title} using get")
        library_content = library.all()
        for content in library_content:
            if content.title == show_name:
                return content
        logging.warning(f"Could not find {show_name} in {library.title} using all()")
        return None


def get_season(plex, show_name, season_num):
    for section in plex.library.sections():
        for content in section.search(show_name):
            if content.type == "show":
                for season in content.seasons():
                    if season.index == season_num:
                        return season
    return None


def get_episode(plex, show_name, **kwargs):
    for section in plex.library.sections():
        for content in section.search(show_name):
            if content.type == "show":
                if "name" in kwargs:
                    return content.episode(title=kwargs["name"])
                elif "season" in kwargs and "episode" in kwargs:
                    try:
                        return content.episode(season=kwargs["season"], episode=kwargs["episode"])
                    except plexapi.exceptions.NotFound:
                        return None
                else:
                    return None


def rating_formatter(rating):
    if rating is None:
        return "N/A"
    else:
        return f"{str(round(rating * 10)).zfill(2)}%"


def get_afs_rating(content, database):
    if content.type == "movie" or content.type == "episode":
        media = database.get_table("plex_watched_media").get_row(media_guid=content.guid)
        if media is not None:
            ratings = media.get("plex_afs_ratings")
            total = sum([rating['rating'] for rating in ratings])
            if len(ratings) == 0:
                return "AFS: `N/A`"
            return f"AFS :`{round(total / len(ratings))}%`"
        return "AFS: `N/A`"
    elif content.type == "show":
        strings = []
        media = database.get_table("plex_watched_media").get_row(media_guid=content.guid)
        if media is not None:
            ratings = media.get("plex_afs_ratings")
            total = sum([rating['rating'] for rating in ratings])
            if len(ratings) != 0:
                strings.append(f"ASS: `{round(total / len(ratings))}%`")
            else:
                strings.append(f"ASS: `N/A`")
        else:
            strings.append("ASS: `N/A`")
        # If there is no rating for the show, get the average rating of all the episodes
        ratings = []
        for episode in content.episodes():
            media = database.get_table("plex_watched_media").get_row(media_guid=episode.guid)
            if media is not None:
                ratings += media.get("plex_afs_ratings")
        total = sum([rating['rating'] for rating in ratings])
        if len(ratings) != 0:
            strings.append(f"AES: `{round(total / len(ratings))}%`")
        else:
            strings.append("AES: `N/A`")

        return " | ".join(strings)
    else:
        logging.debug(f"Content type {content.type} not supported")
        return None


def rating_str(content, database=None) -> str:
    """Get the rating string for a media"""

    rating_strings = [f"`{content.contentRating}`" if hasattr(content, 'contentRating') else "`N/A`"]
    if hasattr(content, 'audienceRating'):
        rating_strings.append(f"Audience `{rating_formatter(content.audienceRating)}`")
    if hasattr(content, 'rating'):
        rating_strings.append(f"Critics  `{rating_formatter(content.rating)}`")
    if database is not None:
        rating_strings.append(get_afs_rating(content, database))

    return " | ".join(rating_strings)


def stringify(objects: [], separator: str = ", ", max_length: int = -1) -> str:
    """Convert a list of genres to a string"""
    str_objects = []
    if max_length == -1:
        max_length = len(objects)
    for obj in objects[:max_length]:
        if isinstance(obj, str):
            str_objects.append(obj)
        elif hasattr(obj, "title"):
            str_objects.append(obj.title)
        elif hasattr(obj, "tag"):
            str_objects.append(obj.tag)
        else:
            pass

    # If there are more than max_length objects, add a +n more to the end
    if len(objects) > max_length:
        str_objects.append(f"+{len(objects) - max_length} more")

    if len(str_objects) == 0:
        return "None"
    for item in str_objects:
        if not isinstance(item, str):
            return f"Something went wrong, unexpected object in stringify\n`{type(item)}`"
    return separator.join(str_objects)


def safe_field(field_text: str) -> str:
    """Make sure the field text follows all the rules for a field"""
    if field_text is None or field_text == "":
        return "N/A"
    elif len(field_text) > 1024:
        return field_text[:1020] + "..."
    else:
        return field_text


def get_series_duration(content: typing.Union[plexapi.video.Show, plexapi.video.Season]) -> int:
    """Get the total duration of a series"""
    total_duration = 0
    for episode in content.episodes():
        try:
            total_duration += episode.duration
        except TypeError:
            pass
    return total_duration


def get_series_size(content: typing.Union[plexapi.video.Show, plexapi.video.Season]) -> int:
    """Get the total size of a series"""
    total_size = 0
    for episode in content.episodes():
        try:
            for media in episode.media:
                for part in media.parts:
                    total_size += part.size
        except TypeError:
            logging.debug(f"Episode {episode.title} has no size")
    return total_size


def make_episode_selector(season, callback) -> typing.Union[typing.List[Select], Button] or None:
    """Make an episode selector for a show"""
    if len(season.episodes()) == 0:
        return None
    elif len(season.episodes()) <= 25:
        select_things = Select(custom_id=f"content_search_{hash(season)}", placeholder="Select an episode",
                               max_values=1)
        for result in season.episodes():
            select_things.add_option(
                label=f"Episode {result.index}: {result.title}",
                value=f"e_{result.grandparentTitle}_{result.parentIndex}_{result.index}_{hash(result)}",
                default=False,
            )
    else:
        # If there are more than 25 episodes, make a selector for every 25 episodes
        split_episodes = [season.episodes()[i: i + 25] for i in range(0, len(season.episodes()), 25)]
        select_things = []
        for i in range(len(split_episodes)):
            select = Select(custom_id=f"content_search_{hash(season)}_{i}", placeholder="Select an episode",
                            max_values=1)
            for result in split_episodes[i]:
                select.add_option(
                    label=f"Episode {result.index}: {result.title}",
                    value=f"e_{result.grandparentTitle}_{result.parentIndex}_{result.index}_{hash(result)}",
                    default=False,
                )
            select_things.append(select)
    cancel_button = Button(style=ButtonStyle.red, label="Cancel", custom_id=f"content_search_cancel_{hash(season)}")
    view = View(timeout=60)
    if isinstance(select_things, list):
        for select in select_things:
            select.select_callback = callback
            view.add_item(select)
    else:
        select_things.callback = callback
        view.add_item(select_things)
    view.add_item(cancel_button)
    return view


def make_season_selector(show, callback) -> typing.Union[typing.List[Select], Button] or None:
    """Make a season selector for a show"""
    if len(show.seasons()) == 0:
        return None
    elif len(show.seasons()) <= 25:
        # select_things = [Select(
        #     custom_id=f"content_search_{hash(show)}",
        #     placeholder="Select a season",
        #     options=[
        #         SelectOption(
        #             label=f"Season {result.index}",
        #             value=f"s_{result.parentTitle}_{result.index}_{hash(result)}",
        #             default=False,
        #         ) for result in show.seasons()
        #     ],
        # )]
        select_things = Select(custom_id=f"content_search_{hash(show)}", placeholder="Select a season",
                               max_values=1)
        select_things.callback = callback
        for result in show.seasons():
            select_things.add_option(
                label=f"Season {result.index}",
                value=f"s_{result.parentTitle}_{result.index}_{hash(result)}",
                default=False,
            )
    else:
        # If there are more than 25 seasons, make a selector for every 25 seasons
        split_seasons = [show.seasons()[i: i + 25] for i in range(0, len(show.seasons()), 25)]
        select_things = []
        for i in range(len(split_seasons)):
            select = Select(custom_id=f"content_search_{hash(show)}_{i}", placeholder="Select a season",
                            max_values=1)
            for result in split_seasons[i]:
                select.add_option(
                    label=f"Season {result.index}",
                    value=f"s_{result.parentTitle}_{result.index}_{hash(result)}",
                    default=False,
                )
            select_things.append(select)

    cancel_button = Button(
        label="Cancel",
        style=ButtonStyle.red,
        custom_id=f"cancel_{hash(show)}",
    )
    view = View()
    if isinstance(select_things, list):
        for select in select_things:
            view.add_item(select)
            select.select_callback = callback
    else:
        view.add_item(select_things)
        select_things.callback = callback
    view.add_item(cancel_button)
    cancel_button.callback = callback
    return view


def base_info_layer(embed, content, database=None, full=True):
    """Make the base info layer for a media"""

    media_info = get_media_info(content.media)

    embed.add_field(name="Ratings", value=rating_str(content, database), inline=False)
    rounded_duration = round(content.duration / 1000)  # Convert time to seconds and round

    if hasattr(content, 'genres'):
        embed.add_field(name="Genres", value=stringify(content.genres, max_length=6), inline=True)
        embed.add_field(name="Runtime", value=f"{datetime.timedelta(seconds=rounded_duration)}", inline=True)
    else:
        embed.add_field(name="Runtime", value=f"{datetime.timedelta(seconds=rounded_duration)}", inline=True)
        count = get_session_count(content, database)
        embed.add_field(name="Watch Sessions",
                        value=f"{'No sessions' if count == 0 else ('Not Available' if count == -1 else count)}",
                        inline=True)

    if database:
        embed.add_field(name="Watch Time", value=f"{get_watch_time(content, database)}", inline=True)
    actors = content.roles
    if len(actors) == 0:
        embed.add_field(name="Cast", value="No information available", inline=False)
    elif len(actors) <= 3:
        embed.add_field(name="Starring", value=stringify(actors, max_length=3), inline=False)
    else:
        embed.add_field(name="Cast", value=stringify(actors, max_length=10 if full else 5), inline=False)

    embed.add_field(name="Producers", value=stringify(content.producers, max_length=4), inline=True)
    embed.add_field(name="Directors", value=stringify(content.directors, max_length=4), inline=True)
    embed.add_field(name="Writers", value=stringify(content.writers, max_length=4), inline=True)
    embed.add_field(name="Media", value=safe_field("\n\n".join(media_info)), inline=False)
    embed.add_field(name="Subtitles",
                    value=safe_field("\n".join(subtitle_details(content, max_subs=6 if full else 2))), inline=False)


def base_user_layer(user: CombinedUser, database):
    accountID = user.plex_user.id
    embed = discord.Embed(title=f"User: {user.display_name(plex_only=True)} - {user.plex_user.id}", color=0x00ff00)
    embed.set_author(name=f"{user.display_name(discord_only=True)} ({user.full_discord_username})",
                     icon_url=user.avatar_url(discord_only=True))
    embed.set_thumbnail(url=user.avatar_url(plex_only=True))
    # The description of a user will contain the following:
    # - How many media items the user has watched
    # - The total duration of the media items the user has watched
    # - How many devices the user has watched on

    # Get the number of media items the user has watched
    num_media = database.get(
        '''SELECT COUNT(*) FROM plex_history_events WHERE account_id = ?''', (accountID,))[0][0]

    # Get the total duration of the media sessions the user has watched
    session_duration = database.get(
        '''SELECT SUM(session_duration) FROM plex_history_events WHERE account_id = ?''', (accountID,))[0][0]

    media_duration = database.get(
        '''SELECT SUM(watch_time) FROM plex_history_events WHERE account_id = ?''', (accountID,))[0][0]

    if session_duration is None:
        session_duration = "Unknown"
    else:
        session_duration = datetime.timedelta(seconds=round(session_duration / 1000))

    if media_duration is None:
        media_duration = "Unknown"
    else:
        media_duration = datetime.timedelta(seconds=round(media_duration / 1000))

    embed.description = f"{user.mention()} has spent `{session_duration}` watching `{num_media}` media sessions " \
                        f"totaling `{media_duration}` on `{len(user.devices)}` devices"

    # Display the last 6 media items the user has watched

    # In order to get the last 6 media items the user has watched, we need to get the last 6 history events
    # And then using the foreign key (media_id) we can get the media item from plex_watched_media

    history_table = database.get_table("plex_history_events")
    media_table = database.get_table("plex_watched_media")
    history_events = history_table.select(f"account_id = '{accountID}'", order_by="history_time DESC", limit=6)
    media_list = []
    for row in history_events:
        media = row.get("plex_watched_media")[0] if len(row.get("plex_watched_media")) > 0 else None
        if media is None:
            continue
        timestamp = datetime.datetime.fromtimestamp(int(row['history_time']), tz=datetime.timezone.utc)
        dynamic_time = f"<t:{round(timestamp.timestamp())}:f>"
        media_duration = datetime.timedelta(seconds=round((row["watch_time"] / 1000)))
        if media_duration < datetime.timedelta(seconds=1):
            media_duration = "Unknown"
        session_duration = datetime.timedelta(seconds=round(row["session_duration"] / 1000))
        if media['media_type'] == "episode":
            show = media_table.get_row(media_id=media['show_id'])
            if show is None:
                logging.warning(f"Could not find show with id {media['show_id']} for media {media}")
                continue
            media_list.append(f"`{show['title']} (S{str(media['season_num']).zfill(2)}E"
                              f"{str(media['ep_num']).zfill(2)})` `[{media_duration}]`\n"
                              f"└─>{dynamic_time} for `{session_duration}`")
        else:
            media_list.append(f"`{media['title']} ({media['media_year']})` `[{media_duration}]`\n"
                              f"└─>{dynamic_time} for `{session_duration}`")
    embed.add_field(name="Last 6 media sessions", value=stringify(media_list, separator='\n'), inline=False)

    # Display the last 6 devices the user has watched on
    last_devices = user.devices[:6]
    device_list = []

    for device in last_devices:
        # For each device calculate the total duration the user has watched on that device
        dynamic_time = f"<t:{round(device.last_seen)}:f>"
        device_duration = database.get("""
        SELECT SUM(watch_time) FROM plex_history_events WHERE account_id = ? AND device_id = ?
        """, (accountID, device.clientIdentifier))[0][0]
        if device_duration is None:
            duration_string = "Unknown"
        else:
            duration_string = datetime.timedelta(seconds=round(device_duration / 1000))
        device_list.append(f"`{device.name}[{device.platform.capitalize()}] - {duration_string}"
                           f"`\n└─>{dynamic_time}")
    embed.add_field(name="Last 6 devices", value=stringify(device_list, separator='\n'), inline=False)
    return embed


def text_progress_bar_maker(duration: float, end: float, start: float = 0, length: int = 54) -> str:
    """
    Make a elapsed time bar using -'s and different sized ▋'s to represent the elapsed time
    :param length: The length of the bar in characters
    :param duration: The duration of the media in any units as long as they are the same
    :param end: The end time of the media in the same units as duration
    :param start: The start time of the media in the same units as duration
    :return: A string of the elapsed time bar ex: <----████------------------->
    """
    length = length - 2
    if duration == 0:
        return "N/A"
    if start > end:
        temp = end
        end = start
        start = temp

    front_porch = int((start / duration) * length)
    back_porch = int((duration - end) / duration * length)
    elapsed = max(length - front_porch - back_porch, 1)
    bar = f"`<{'-' * front_porch}{'=' * elapsed}{'-' * back_porch}>`"
    return bar


def get_watch_time(content, db) -> datetime.timedelta:
    """Get the total watch time of a piece of content from the plex_history_events table"""
    media_table = db.get_table("plex_watched_media")
    if isinstance(content, plexapi.video.Movie):
        media = media_table.get_row(media_guid=content.guid, media_type="movie")
        if media is None:
            return datetime.timedelta(seconds=0)
        result = db.get('''SELECT SUM(watch_time) FROM plex_history_events WHERE media_id = ?''', (media['media_id'],))
    elif isinstance(content, plexapi.video.Show):
        media = media_table.get_row(media_guid=content.guid, media_type="show")
        if media is None:
            logging.warning(f"Could not find {content.title} in the database")
            return datetime.timedelta(seconds=0)
        result = db.get(
            f'''SELECT SUM(watch_time) FROM plex_history_events WHERE media_id in 
            (SELECT media_id FROM plex_watched_media WHERE show_id = {media['media_id']})''')
    elif isinstance(content, plexapi.video.Season):
        media = media_table.get_row(media_guid=content.parentGuid, media_type="show")
        if media is None:
            logging.warning(f"Could not find {content.title} in the database")
            return datetime.timedelta(seconds=0)
        result = db.get(
            '''SELECT SUM(watch_time) FROM plex_history_events WHERE media_id in 
            (SELECT media_id FROM plex_watched_media WHERE show_id = ? AND season_num = ?)''',
            (media['media_id'], content.seasonNumber))
    elif isinstance(content, plexapi.video.Episode):
        media = media_table.get_row(media_guid=content.guid, media_type="episode")
        if media is None:
            return datetime.timedelta(seconds=0)
        result = db.get('''SELECT SUM(watch_time) FROM plex_history_events WHERE media_id = ?''', (media['media_id'],))
    else:
        raise TypeError("content must be a plexapi video object")
    print(result)
    if result[0][0] is None:
        logging.warning(f"Watch time for {content.title} was None")
        return datetime.timedelta(seconds=0)
    return datetime.timedelta(seconds=round(result[0][0] / 1000))


def get_session_count(content, db) -> int:
    """Get the total number of sessions of a piece of content from the plex_history_events table"""
    media_table = db.get_table("plex_watched_media")
    if isinstance(content, plexapi.video.Movie):
        media = media_table.get_row(media_guid=content.guid)
        if media is None:
            return -1
        result = db.get('''SELECT COUNT(*) FROM plex_history_events WHERE media_id = ?''', (media['media_id'],))
    elif isinstance(content, plexapi.video.Show):
        media = media_table.get_row(media_guid=content.guid)
        if media is None:
            return -1
        result = db.get(
            f'''SELECT COUNT(*) FROM plex_history_events WHERE media_id in 
            (SELECT media_id FROM plex_watched_media WHERE show_id = {media['media_id']})''')
    elif isinstance(content, plexapi.video.Episode):
        media = media_table.get_row(media_guid=content.guid)
        if media is None:
            return -1
        result = db.get('''SELECT COUNT(*) FROM plex_history_events WHERE media_id = ?''', (media['media_id'],))
    else:
        raise TypeError("content must be a plexapi video object")

    return result[0][0]
