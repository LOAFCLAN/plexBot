import datetime
import re

import discord

__all__ = ['clean', 'is_clean']

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

        if len(session.media) > 1:
            # Find which media file has a bitrate closest to the reserved bitrate
            reserved_bitrate = session.session[0].bandwidth
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

        timeline = f"{current_position} / {total_duration} - {str(session.players[0].state).capitalize()}"
        if len(session.session) == 0:
            bandwidth = "Invalid encoding, they probably need help"
        else:
            if session.session[0].location.startswith("lan"):
                bandwidth = "Local session, no bandwidth reserved"
            else:
                if media is None:
                    bandwidth = "Unknown media"
                else:
                    bandwidth = f"{round(media.bitrate)} kbps of bandwidth reserved"
                    total_bandwidth += media.bitrate

        media_info = "`Media info unavailable`"
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
                    f"{bandwidth}\n" \
                    f"{media_info}"
        elif session.type == 'episode':
            value = f"{session.grandparentTitle} - `{session.parentTitle}`\n" \
                    f"{session.title} - `Episode {session.index}`\n" \
                    f"{timeline}\n" \
                    f"{bandwidth}\n" \
                    f"{media_info}"
        else:
            value = f"{session.title} - {session.type}\n" \
                    f"{timeline}\n" \
                    f"{bandwidth}\n" \
                    f"{media_info}"
        # print(session.players[0].__dict__)
        embed.add_field(name=f"{plex.associations.display_name(session.usernames[0])} on {device}", value=value,
                        inline=False)

    embed.timestamp = datetime.datetime.utcnow()
    embed.set_footer(text=f"{round(total_bandwidth)} kps of bandwidth reserved")
    return embed
