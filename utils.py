import datetime

import discord


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

        current_position = datetime.timedelta(seconds=round(session.viewOffset / 1000))
        total_duration = datetime.timedelta(seconds=round(session.duration / 1000))

        timeline = f"{current_position} / {total_duration} - {str(session.players[0].state).capitalize()}"
        bandwidth = f"{round(session.session[0].bandwidth)} kbps of bandwidth reserved"
        total_bandwidth += session.session[0].bandwidth

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
                    f"{bandwidth}"
        elif session.type == 'episode':
            value = f"{session.grandparentTitle} - `{session.parentTitle}`\n" \
                    f"{session.title} - `Episode {session.index}`\n" \
                    f"{timeline}\n" \
                    f"{bandwidth}"
        else:
            value = f"{session.title} - {session.type}\n" \
                    f"{timeline}"
        # print(session.players[0].__dict__)
        embed.add_field(name=f"{session.usernames[0]} on {device}", value=value, inline=False)

    embed.timestamp = datetime.datetime.utcnow()
    embed.set_footer(text=f"{round(total_bandwidth)} kps of bandwidth reserved")
    return embed