import datetime
import re
import traceback

import langcodes
import humanize
import plexapi
import typing
from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction
import discord

__all__ = ['clean', 'is_clean', 'get_season', 'base_info_layer', 'rating_str', 'stringify', 'make_season_selector',
           'make_episode_selector', 'cleanup_url', 'get_episode', 'text_progress_bar_maker']

from plex_wrappers import CombinedUser

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


def get_all_library(plex):
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
        print(f"Translation error: {e}\n{traceback.format_exc()}")
        return f"{lang}*"


async def session_embed(plex):
    plex_sessions = plex.sessions()
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

        if not session.isFullObject:
            session.reload(checkFiles=False)
            if not session.isFullObject:
                raise Exception("Session is still partial")

        if isinstance(session.session, list):
            session_instance = session.session[0]
        elif isinstance(session.session, plexapi.media.Session):
            session_instance = session.session
        else:
            if len(session.usernames) == 0:
                embed.add_field(name=f"{session.title}", value="Session has no users", inline=False)
            else:
                embed.add_field(name=f"{session.usernames[0]} has encountered an error",
                                value=f"Invalid session type, {type(session.session)}", inline=False)
            continue

        if len(session.media) > 1:
            # Find which media file has a bitrate closest to the reserved bitrate
            reserved_bitrate = session_instance.bandwidth
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

        current_position = datetime.timedelta(seconds=round(session.viewOffset / 1000))
        total_duration = datetime.timedelta(seconds=round(session.duration / 1000))

        progress_bar = text_progress_bar_maker(duration=total_duration.total_seconds(),
                                               end=current_position.total_seconds(), length=35)

        timeline = f"{current_position} / {total_duration} - {str(session.players[0].state).capitalize()}"

        if session_instance.location.startswith("lan"):
            bandwidth = "Local session, no bandwidth reserved"
        else:
            if media is None:
                bandwidth = "Unknown media"
            else:
                bandwidth = f"`{round(media.bitrate)}` kbps of bandwidth reserved"
                total_bandwidth += media.bitrate

        if len(session.transcodeSessions) == 0:
            media_info = f"`{media.container}` - `{media.videoCodec}:" \
                         f" {media.width}x{media.height}@{media.videoFrameRate} " \
                         f"| {media.audioCodec}: {media.audioChannels}ch`"
        elif len(session.transcodeSessions) == 1:
            transcode = session.transcodeSessions[0]
            if transcode.videoDecision == "transcode" or transcode.audioDecision == "transcode":
                media_info = f"`{transcode.sourceVideoCodec}:{transcode.sourceAudioCodec}" \
                             f"`->`{transcode.videoCodec}:{transcode.audioCodec}`"
            else:
                media_info = f"`{media.container}` - `{media.videoCodec}:" \
                             f" {media.width}x{media.height}@{media.videoFrameRate} " \
                             f"| {media.audioCodec}: {media.audioChannels}ch`"
        else:
            media_info = "`Multiple transcode sessions detected!`"

        if session.players[0].title:
            device = session.players[0].title
        elif session.players[0].model:
            device = session.players[0].model
        else:
            device = session.players[0].platform

        # print(session.__dict__)
        # print(session.session[0].__dict__)

        if session.type == 'movie':
            value = f"{session.title} ({session.year})\n" \
                    f"{timeline}\n" \
                    f"{progress_bar}\n" \
                    f"{bandwidth}\n" \
                    f"{media_info}"
        elif session.type == 'episode':
            value = f"{session.grandparentTitle} - `{session.parentTitle}`\n" \
                    f"{session.title} - `Episode {session.index}`\n" \
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

    embed.timestamp = datetime.datetime.utcnow()
    embed.set_footer(text=f"{round(total_bandwidth)} kps of bandwidth reserved")
    return embed


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
                # if part.deepAnalysisVersion != 6:
                #     # Send a command to the plex sever to run a deep analysis on this part
                #     this_media = f"`File#{media_index}`: `{media.videoCodec}:{media.width}x" \
                #                  f"{media.height}@{media.videoFrameRate} " \
                #                  f"| {media.audioCodec}: {media.audioChannels}ch`\n" \
                #                  f"┕──> `Insufficient deep analysis data, L:{part.deepAnalysisVersion}`"
                #
                # else:
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
                    return content.episode(season=kwargs["season"], episode=kwargs["episode"])
                else:
                    return None


def rating_formatter(rating):
    if rating is None:
        return "N/A"
    else:
        return f"{round(rating * 10)}%"


def rating_str(content) -> str:
    """Get the rating string for a media"""
    if hasattr(content, 'audienceRating') and hasattr(content, 'rating'):
        rating_string = f"`{content.contentRating}` | " \
                        f"Audience `{rating_formatter(content.audienceRating)}`" \
                        f" | Critics `{rating_formatter(content.rating)}`"
    else:
        rating_string = "No ratings available"
    return rating_string


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
        total_duration += episode.duration
    return total_duration


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


def base_info_layer(embed, content):
    """Make the base info layer for a media"""

    media_info = get_media_info(content.media)

    embed.add_field(name="Ratings", value=rating_str(content), inline=False)
    rounded_duration = round(content.duration / 1000)  # Convert time to seconds and round

    if hasattr(content, 'genres'):
        embed.add_field(name="Genres", value=stringify(content.genres, max_length=6), inline=True)
    else:
        embed.add_field(name="Genres", value="Not applicable", inline=True)

    embed.add_field(name="Runtime", value=f"{datetime.timedelta(seconds=rounded_duration)}", inline=True)
    actors = content.roles
    if len(actors) == 0:
        embed.add_field(name="Cast", value="No information available", inline=False)
    elif len(actors) <= 3:
        embed.add_field(name="Starring", value=stringify(actors, max_length=3), inline=False)
    else:
        embed.add_field(name="Cast", value=stringify(actors, max_length=10), inline=False)

    embed.add_field(name="Producers", value=stringify(content.producers, max_length=4), inline=True)
    embed.add_field(name="Directors", value=stringify(content.directors, max_length=4), inline=True)
    embed.add_field(name="Writers", value=stringify(content.writers, max_length=4), inline=True)
    embed.add_field(name="Media", value=safe_field("\n\n".join(media_info)), inline=False)
    embed.add_field(name="Subtitles",
                    value=safe_field("\n\n".join(subtitle_details(content, max_subs=6))), inline=False)


def base_user_layer(user: CombinedUser, database):
    accountID = user.plex_system_account.id
    embed = discord.Embed(title=f"User: {user.display_name(plex_only=True)} - {user.plex_user.id}", color=0x00ff00)
    embed.set_author(name=f"{user.display_name(discord_only=True)} ({user.full_discord_username()})",
                     icon_url=user.avatar_url(discord_only=True))
    embed.set_thumbnail(url=user.avatar_url(plex_only=True))
    # The description of a user will contain the following:
    # - How many media items the user has watched
    # - The total duration of the media items the user has watched
    # - How many devices the user has watched on

    # Get the number of media items the user has watched
    num_media = database.execute(
        '''SELECT COUNT(*) FROM plex_history_messages WHERE account_ID = ?''', (accountID,)).fetchone()[0]

    # Get the total duration of the media items the user has watched
    session_duration = database.execute(
        '''SELECT SUM(session_duration) FROM plex_history_messages WHERE account_ID = ? and session_duration > 0''', (accountID,)).fetchone()[0]
    media_duration = database.execute(
        '''SELECT SUM(pb_end_offset - pb_start_offset) FROM plex_history_messages WHERE account_ID = ? 
        AND pb_end_offset > 0''',
        (accountID,)).fetchone()[0]
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
    last_media = database.execute(
        '''SELECT * FROM plex_history_messages WHERE account_ID = ? ORDER BY history_time DESC LIMIT 6''',
        (accountID,)).fetchall()
    media_list = []
    for row in last_media:
        timestamp = datetime.datetime.fromtimestamp(row[3], tz=datetime.timezone.utc)
        dynamic_time = f"<t:{round(timestamp.timestamp())}:f>"
        media_duration = datetime.timedelta(seconds=round((row[10] - row[9]) / 1000))
        session_duration = datetime.timedelta(seconds=round(row[12] / 1000))
        if row[5] == "episode":
            media_list.append(f"`{row[4]} (S{str(row[6]).zfill(2)}E{str(row[7]).zfill(2)})` `[{media_duration}]`\n"
                              f"└─>{dynamic_time} for `{session_duration}`")
        else:
            media_list.append(f"`{row[4]} ({row[11]})` `[{media_duration}]`\n"
                              f"└─>{dynamic_time} for `{session_duration}`")
    embed.add_field(name="Last 6 media sessions", value=stringify(media_list, separator='\n'), inline=False)

    # Display the last 6 devices the user has watched on
    last_devices = user.devices[:6]
    device_list = []

    for device in last_devices:
        dynamic_time = f"<t:{round(device.last_seen)}:f>"
        device_list.append(f"`{device.name}[{device.platform.capitalize()}]`\n└─>{dynamic_time}")
    embed.add_field(name="Last 6 devices", value=stringify(device_list, separator='\n'), inline=False)
    return embed


def text_progress_bar_maker(duration: float, end: float, start: float = 0, length: int = 55) -> str:
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
