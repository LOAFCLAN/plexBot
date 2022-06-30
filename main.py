import datetime
import os
import traceback
from decimal import InvalidContext

import sqlite3

from discord.ext import commands
from plexapi.server import PlexServer
import discord

activity = PlexServer.activities

database = sqlite3.connect('plex_bot.db')
database.execute('''CREATE TABLE IF NOT EXISTS bot_config (token TEXT, prefix TEXT)''')
database.commit()

# Get the bot's prefix from the database
cursor = database.execute('''SELECT * FROM bot_config''')
config = cursor.fetchone()
if config is None:
    cursor = database.execute('''INSERT INTO bot_config VALUES (?, ?)''', ('', '!'))
    database.commit()
    config = database.cursor()
    print("No config found, created one")
    print("Please set the bot's prefix and token in the database")
    token = input("Token: ")
    prefix = input("Prefix: ")
    database.execute('''UPDATE bot_config SET token = ?, prefix = ?''', (token, prefix))
    database.commit()
    print("Config set, restarting...")
    os.execl(__file__, 'python3', 'main.py')
bot = discord.ext.commands.Bot(command_prefix=config[1])
other_token = config[0]


def get_all_library(plex):
    all_library = []
    for library in plex.library.sections():
        all_library.append(library)
    return all_library


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


@bot.command(name="update", is_owner=True)
async def update(ctx):
    """Update the bot from the master branch"""
    msg = await ctx.send("Updating...")
    res = os.popen("git pull").read()
    if res.startswith('Already up to date.'):
        await ctx.send('```\n' + res + '```')
    else:
        await ctx.send('```\n' + res + '```')
        # Run pip update on requirements.txt
        res = os.popen("pip install -r requirements.txt").read()
        await ctx.send('```\n' + res + '```')
        await msg.edit(content="Restarting...")
        # await ctx.bot.get_command('restart').callback(self, ctx)

    await msg.delete()


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print(f'Bot ID: {bot.user.id}')
    # To get the activity message IDs and channel IDs


@bot.event
async def on_command_error(context, exception):
    if isinstance(exception, commands.NoPrivateMessage):
        await context.send('{}, This command cannot be used in DMs.'.format(context.author.mention))
    elif isinstance(exception, commands.UserInputError):
        pass  # Silent ignore
        # await context.send('{}, {}'.format(context.author.mention, self.format_error(context, exception)))
    elif isinstance(exception, commands.NotOwner):
        await context.send('{}, {}'.format(context.author.mention, exception.args[0]))
    elif isinstance(exception, commands.MissingPermissions):
        permission_names = [name.replace('guild', 'server').replace('_', ' ').title() for name in
                            exception.missing_perms]
        # await context.send('{}, you need {} permissions to run this command!'.format(
        #     context.author.mention, utils.pretty_concat(permission_names)))
    elif isinstance(exception, commands.BotMissingPermissions):
        permission_names = [name.replace('guild', 'server').replace('_', ' ').title() for name in
                            exception.missing_perms]
        # await context.send('{}, I need {} permissions to run this command!'.format(
        #     context.author.mention, utils.pretty_concat(permission_names)))
    elif isinstance(exception, commands.CommandOnCooldown):
        await context.send(
            '{}, That command is on cooldown! Try again in {:.2f}s!'.format(context.author.mention,
                                                                            exception.retry_after))
    elif isinstance(exception, commands.MaxConcurrencyReached):
        types = {discord.ext.commands.BucketType.default: "`Global`", discord.ext.commands.BucketType.guild: "`Guild`",
                 discord.ext.commands.BucketType.channel: "`Channel`",
                 discord.ext.commands.BucketType.category: "`Category`",
                 discord.ext.commands.BucketType.member: "`Member`", discord.ext.commands.BucketType.user: "`User`"}
        await context.send(
            '{}, That command has exceeded the max {} concurrency limit of `{}` instance! Please try again later.'.format(
                context.author.mention, types[exception.per], exception.number))
    elif isinstance(exception, (commands.CommandNotFound, InvalidContext)):
        pass  # Silent ignore
    else:
        await context.send(
            '```\n%s\n```' % ''.join(traceback.format_exception_only(type(exception), exception)).strip())
        if isinstance(context.channel, discord.TextChannel):
            pass
            # DOZER_LOGGER.error('Error in command <%d> (%d.name!r(%d.id) %d(%d.id) %d(%d.id) %d)',
            #                    context.command, context.guild, context.guild, context.channel, context.channel,
            #                    context.author, context.author, context.message.content)
        else:
            pass
        #     DOZER_LOGGER.error('Error in command <%d> (DM %d(%d.id) %d)', context.command, context.channel.recipient,
        #                        context.channel.recipient, context.message.content)
        # DOZER_LOGGER.error(''.join(traceback.format_exception(type(exception), exception, exception.__traceback__))


database.execute(
    '''CREATE TABLE IF NOT EXISTS plex_servers (guild_id INTEGER PRIMARY KEY, server_url TEXT, 
    server_token TEXT);''')
database.execute(
    '''CREATE TABLE IF NOT EXISTS discord_associations (discord_id INTEGER PRIMARY KEY, plex_id INTEGER, plex_email 
    TEXT, plex_username TEXT);''')
database.execute(
    '''CREATE TABLE IF NOT EXISTS activity_messages (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, message_id 
    INTEGER);''')
database.commit()

bot.database = database
for extension in bot.extensions:
    bot.unload_extension(extension)
bot.load_extension('plexBot')

while True:
    try:
        print("Starting Plex Bot")
        bot.run(other_token)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(e)
        traceback.print_exc()
        # print("Restarting...")
        # time.sleep(5)
        break
