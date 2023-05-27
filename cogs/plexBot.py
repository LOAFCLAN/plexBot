import asyncio
import datetime
import random
import traceback
import typing

import plexapi.alert
from discord.ext.commands import command, has_permissions, Cog, BadArgument
import discord.errors as discord_errors
import discord

from utils import get_all_library, session_embed, base_user_layer, get_from_media_index, base_info_layer, safe_field

from loguru import logger as logging

from wrappers_utils.BotExceptions import PlexNotLinked, PlexNotReachable


class PlexBot(Cog):

    @has_permissions(administrator=True)
    @command(name="user_add", aliases=["add_user", "adduser", "useradd"])
    async def user_add(self, ctx, plex_id):
        celery = ctx.plex().myPlexAccount()
        pending = celery.pendingInvites()
        if len(pending) == 0:
            embed = discord.Embed(title="Add User", description="There are currently no pending invites, "
                                                                "user was not added", color=0xFF0000)
            embed.timestamp = datetime.datetime.now()
            await ctx.send(embed=embed)
            return
        for invite in pending:
            if invite.username == plex_id:
                embed = discord.Embed(title="Add User", description=f"User `{invite.username}` was added",
                                      color=0x00ff00)
                celery.acceptInvite(invite.username)
                celery.inviteFriend(invite.email, ctx.plex, get_all_library(ctx.plex))
                movie_library_string = ""
                for library in get_all_library(ctx.plex):
                    if library.type == "movie":
                        movie_library_string += f"`{library.title}` (Size: `{library.totalSize}`)\n"
                embed.add_field(name="Movie Library's", value=movie_library_string, inline=True)
                show_library_string = ""
                for library in get_all_library(ctx.plex):
                    if library.type == "show":
                        show_library_string += f"`{library.title}` (Size: `{library.totalSize}`)\n"
                embed.add_field(name="Show Library's", value=show_library_string, inline=True)
                embed.timestamp = datetime.datetime.now()
                await ctx.send(embed=embed)
                return ctx.plex().getUser(plex_id)

        embed = discord.Embed(title="Add User", description="User was not found in pending invites", color=0xFF0000)
        embed.timestamp = datetime.datetime.now()
        await ctx.send(embed=embed)

    def __init__(self, bot):
        self.bot = bot
        table = self.bot.database.get_table("activity_messages")
        self.activity_messages = table.get_all()
        table = self.bot.database.get_table("plex_alert_channel")
        self.plex_alert_channels = table.get_all()

        self.event_tracker = {}

    @Cog.listener('on_ready')
    async def on_ready(self):
        logging.info("Cog: PlexBot is ready")
        for message_config in self.activity_messages:
            self.bot.loop.create_task(self.monitor_plex(message_config[0], message_config[1], message_config[2]))
        for alert_config in self.plex_alert_channels:
            self.bot.loop.create_task(self.plex_alerts(alert_config[0], alert_config[1]))
        self.bot.loop.create_task(self.status_update())

    async def status_update(self):
        """Update plexbots status every 10 seconds to show the current number of sessions across all servers"""
        while True:
            try:
                await asyncio.sleep(10)
                total_sessions = 0
                for guild in self.bot.guilds:
                    try:
                        plex = await self.bot.fetch_plex(guild)
                        total_sessions += len(plex.sessions())
                    except PlexNotLinked:
                        continue
                    except PlexNotReachable:
                        continue
                if total_sessions == 0:
                    await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,
                                                                             name="Plex"))
                elif total_sessions == 1:
                    await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,
                                                                             name=f"{total_sessions} session"))
                else:
                    await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,
                                                                             name=f"{total_sessions} sessions"))
            except Exception as e:
                logging.error(e)
                logging.exception(e)

    async def plex_alerts(self, guild_id: int, channel_id: int):
        """Handles plex alerts"""
        channel = await self.bot.fetch_channel(channel_id)
        guild = await self.bot.fetch_guild(guild_id)
        plex = await self.bot.fetch_plex(guild)

        def alert_received(alert):
            print(f"Alert received: {alert}")

        plex.startAlertListener(alert_received)

    async def monitor_plex(self, guild_id: int, channel_id: int, message_id: int):
        channel = await self.bot.fetch_channel(channel_id)
        guild = await self.bot.fetch_guild(guild_id)
        plex = await self.bot.fetch_plex(guild)
        try:
            logging.info(f"Starting plex monitor for guild: {guild_id}, channel: {channel_id}, message: {message_id}")

            async def create_message():
                new_message = await channel.send(f"Initializing activity monitor")
                self.bot.database.execute("UPDATE activity_messages SET message_id = ? WHERE channel_id = ?",
                                          (new_message.id, channel_id))
                self.bot.database.commit()
                # Pin the new message to the channel
                await new_message.pin()
                # Find the "Message was pinned" message
                async for pin_message in channel.history(limit=1, after=new_message):
                    await pin_message.delete()
                return new_message

            if message_id == 0:
                message = await create_message()
            else:
                try:
                    message = await channel.fetch_message(message_id)
                except discord_errors.NotFound:
                    message = await create_message()

            while True:
                try:
                    embed = await session_embed(plex)
                    await message.edit(embed=embed, content="")
                    await asyncio.sleep(10)
                    # await log_scan()
                    # Dynamic sleep based on our current discord rate limit
                except discord_errors.NotFound:
                    message = await create_message()
                except discord_errors.Forbidden as e:
                    # Check if it's a 50005 error (Different author)
                    if e.code == 50005:
                        message = await create_message()
                        logging.warning(f"Wrong author for updating message, creating new message")
                    else:
                        logging.error(f"Missing permissions to edit message in channel: {channel_id}")
                except Exception as e:
                    print(e)
                    traceback.print_exc()
                    embed = discord.Embed(title="Plex Session Monitor",
                                          description=f"{self.bot.user.name} has encountered an error", color=0xFF0000)
                    embed.add_field(name="Error", value=f"{e}", inline=False)
                    embed.add_field(name="Traceback", value=traceback.format_exc()[:1024], inline=False)
                    embed.timestamp = datetime.datetime.utcnow()
                    await message.edit(embed=embed, content="")
                    await asyncio.sleep(5)
        except Exception as e:
            logging.error(f"Error in plex monitor: {e}")
            await asyncio.sleep(5)
            self.bot.loop.create_task(self.monitor_plex(guild_id, channel_id, message_id))

    @command(name="pending_invites", aliases=["pendinginvites", "pendinginvite", "pending"])
    async def pending_invites(self, ctx):
        celery = ctx.plex.myPlexAccount()
        invites = celery.pendingInvites()
        incoming = celery.pendingInvites(includeSent=False)
        outgoing = celery.pendingInvites(includeReceived=False)
        if len(invites) == 0:
            embed = discord.Embed(title="Pending Invites",
                                  description="There are currently no pending invites", color=0x00ff00)
        elif len(invites) == 1:
            embed = discord.Embed(title="Pending Invites",
                                  description="There is currently **1** pending invite", color=0x00ff00)
        else:
            embed = discord.Embed(title="Pending Invites",
                                  description=f"There are currently **{len(invites)}** pending invites", color=0x00ff00)
        formatted_rows = ""
        for invite in incoming:
            formatted_rows += f"{invite.username} - {invite.email}\n"
        if len(formatted_rows) > 0:
            embed.add_field(name=f"Incoming invites: {len(incoming)}", value=formatted_rows, inline=False)
        else:
            embed.add_field(name=f"Incoming invites: {len(incoming)}", value="No incoming invites", inline=False)

        formatted_rows = ""
        for invite in outgoing:
            formatted_rows += f"{invite.username} - {invite.email}\n"
        if len(formatted_rows) > 0:
            embed.add_field(name=f"Outgoing invites: {len(outgoing)}", value=formatted_rows, inline=False)
        else:
            embed.add_field(name=f"Outgoing invites: {len(outgoing)}", value="No outgoing invites", inline=False)

        embed.timestamp = datetime.datetime.now()
        await ctx.send(embed=embed)

    @has_permissions(manage_guild=True)
    @command(name="invite_friend", aliases=["invite", "invite_user"])
    async def invite_friend(self, ctx, *, plex_id: str):
        celery = ctx.plex.myPlexAccount()
        celery.inviteFriend(plex_id, ctx.plex, get_all_library(ctx.plex))
        movie_library_string = ""
        for library in get_all_library(ctx.plex):
            if library.type == "movie":
                movie_library_string += f"`{library.title}` (Size: `{library.totalSize}`)\n"
        embed = discord.Embed(title="Invite Friend", description="Friend was invited", color=0x00ff00)
        embed.add_field(name="Movie Library's", value=movie_library_string, inline=True)
        show_library_string = ""
        for library in get_all_library(ctx.plex):
            if library.type == "show":
                show_library_string += f"`{library.title}` (Size: `{library.totalSize}`)\n"
        embed.add_field(name="Show Library's", value=show_library_string, inline=True)
        embed.set_footer(text="Check your email to accept the invite")
        embed.timestamp = datetime.datetime.now()
        await ctx.send(embed=embed)

    @has_permissions(manage_guild=True)
    @command(name="cancel_invite", aliases=["cancel"])
    async def cancel_invite(self, ctx, *, plex_id: str):
        celery = ctx.plex.myPlexAccount()
        invite = celery.cancelInvite(plex_id)
        embed = discord.Embed(title="Cancel Invite", description="Invite was canceled", color=0x00ff00)
        embed.timestamp = datetime.datetime.now()

        await ctx.send(embed=embed)

    # @has_permissions(manage_guild=True)
    @command(name="accept_user", aliases=["accept", "au"])
    async def accept_user(self, ctx, user_id):
        await self.user_add(ctx, user_id)

    @has_permissions(administrator=True)
    @command(name="remove_user", aliases=["removeuser", "ru"])
    async def remove_user(self, ctx, user_id):
        celery = ctx.plex.myPlexAccount()
        users = ctx.plex.systemAccounts()
        for user in users[2:]:
            if user.name == user_id:
                embed = discord.Embed(title="Remove User",
                                      description=f"Are you sure you want to remove `{user.name}`?", color=0xffff00)
                embed.set_footer(text="React with ✅ to confirm, or ❎ to cancel")
                msg = await ctx.send(embed=embed)
                await msg.add_reaction("✅")
                await msg.add_reaction("❎")

                def check(msg_reaction, react_user):
                    return react_user == ctx.author and str(msg_reaction.emoji) in ["✅", "❎"]

                try:
                    reaction, msg_user = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
                    if str(reaction.emoji) == "✅":
                        try:
                            celery.removeFriend(user.id)
                            embed = discord.Embed(title="Remove User",
                                                  description=f"User `{user.name}` was removed from "
                                                              f"{ctx.plex.friendlyName}",
                                                  color=0x00ff00)
                            embed.timestamp = datetime.datetime.now()
                            await msg.edit(embed=embed)
                        except Exception as e:
                            embed = discord.Embed(title="Remove User",
                                                  description=f"User `{user.name}` was not removed from "
                                                              f"{ctx.plex.friendlyName}",
                                                  color=0xFF0000)
                            embed.add_field(name="Error", value=f"{e}", inline=False)
                            embed.timestamp = datetime.datetime.now()
                            await msg.edit(embed=embed)
                    elif str(reaction.emoji) == "❎":
                        embed = discord.Embed(title="Remove User",
                                              description=f"User `{user.name}` was not removed from "
                                                          f"{ctx.plex.friendlyName}"
                                              , color=0xFF0000)
                        embed.timestamp = datetime.datetime.now()
                        await msg.edit(embed=embed)
                except asyncio.TimeoutError:
                    embed = discord.Embed(title="Remove User",
                                          description="Timed out waiting for confirmation", color=0xFF0000)
                    embed.timestamp = datetime.datetime.now()
                    await ctx.send(embed=embed)
                    return

    @has_permissions(manage_guild=True)
    @command(name='users')
    async def users(self, ctx):
        celery = ctx.plex.myPlexAccount()
        users = celery.users()
        embed = discord.Embed(title="Users", description="", color=0x00ff00)
        for user in users:
            username = user.username
            email = user.email
            if username is None or len(username) <= 0:
                username = "N/A"
            if email is None or len(email) <= 0:
                email = "N/A"
            embed.add_field(name=f"{username} - {user.id}", value=f"{email}", inline=False)
        embed.timestamp = datetime.datetime.now()
        await ctx.send(embed=embed, delete_after=30)

    @command(name='user', aliases=['u'])
    async def user(self, ctx, user: typing.Union[discord.Member, str]):
        user = ctx.plex.associations.get(user)
        embed = base_user_layer(user, self.bot.database)
        await ctx.send(embed=embed)

    @has_permissions(manage_guild=True)
    @command(name='link', aliases=['link_user'])
    async def link(self, ctx, discord_user: typing.Union[discord.Member, discord.Role], plex_id: str):
        """Link a discord user to a plex user in the bots database"""
        if isinstance(discord_user, discord.Role):
            raise BadArgument("You can't link a role to a plex user")
        print(f"{discord_user.name} is linking to {plex_id}")
        plex_users = ctx.plex_host.users()
        plex_users.append(ctx.plex_host)
        plex_user = None

        for user in plex_users:
            if user.id == plex_id or user.username == plex_id or user.email == plex_id:
                plex_user = user
                break
        if plex_user is None:
            await ctx.send("Plex user not found")
            return
        if discord_user in ctx.plex.associations:
            await ctx.send("User already linked")
            return
        ctx.plex.associations.add_association(ctx.plex, discord_user, plex_user.id, plex_user.username, plex_user.email)
        await ctx.send(f"User {discord_user.mention} linked to {plex_user.username}")

    @command(name='unlink', aliases=['unlink_user'])
    async def unlink(self, ctx, discord_user: discord.Member):
        """Unlink a discord user from a plex user in the bots database"""
        if discord_user not in ctx.plex.associations:
            await ctx.send("User not linked")
            return
        ctx.plex.associations.remove_association(ctx.plex, discord_user)
        await ctx.send(f"User {discord_user.mention} unlinked")

    @command(name='linked', aliases=['linked_users'])
    async def linked(self, ctx):
        """List all linked users"""

        embed = discord.Embed(title="Linked Users",
                              description=f"{len(ctx.plex.associations)} users linked",
                              color=0x00ff00)
        for user in ctx.plex.associations:
            embed.add_field(name=f"{user.display_name(plex_only=True)} - {user.id(plex_only=True)}",
                            value=f"{user.mention()}", inline=False)

        if not ctx.plex.associations.ready:
            embed.set_footer(text="Not all linked users have been loaded, this list may be incomplete")
        embed.timestamp = datetime.datetime.utcnow()
        await ctx.send(embed=embed)

    @command(name='ping')
    async def ping(self, ctx):
        ping_responses = ["Pong!", "What's up", "I'm here!", "I'm here, I'm here!",
                          "I'm here, I'm here, I'm here!"]
        await ctx.send(random.choice(ping_responses))

    @command(name='signup', aliases=['register'])
    async def signup(self, ctx):
        await ctx.send("https://plex.tv/sign-up")

    @command(name='player', aliases=['download'])
    async def download(self, ctx):
        await ctx.send("https://www.plex.tv/media-server-downloads/#plex-app")

    @command(name='sessions')
    async def sessions(self, ctx):
        embed = await session_embed(ctx.plex)
        await ctx.send(embed=embed)

    @has_permissions(manage_guild=True)
    @command(name='plex')
    async def plex_status(self, ctx):
        embed = discord.Embed(title="Plex Status", description="", color=0x00ff00)
        embed.add_field(name="Clients", value=f"{len(ctx.plex().systemAccounts())}", inline=False)
        for client in ctx.plex().systemAccounts():
            if len(client.name) < 1:
                client.name = "Unknown"
            embed.add_field(name=client.name, value=client.key, inline=False)

        await ctx.send(embed=embed)

    @has_permissions(administrator=True)
    @command(name='set_activity_channel', aliases=['setactivitychannel', 'setactivity'])
    async def set_activity_channel(self, ctx, channel: discord.TextChannel):
        """Adds a plex activity channel to the database"""
        table = self.bot.database.get_table("activity_messages")
        table.update_or_add(guild_id=ctx.guild.id, channel_id=channel.id)
        embed = discord.Embed(title="Set Activity Channel", description=f"Set activity channel to {channel.mention}",
                              color=0x00ff00)
        embed.timestamp = datetime.datetime.now()
        await ctx.send(embed=embed)
        self.bot.loop.create_task(self.monitor_plex(ctx.guild.id, channel.id, 0))

    @has_permissions(administrator=True)
    @command(name="set_alert_channel", aliases=["setalertchannel", "setalert"])
    async def set_alert_channel(self, ctx, channel: discord.TextChannel):
        """Adds a plex alert channel to the database"""
        self.bot.database.execute(
            "INSERT INTO plex_alert_channel (guild_id, channel_id) VALUES (?, ?)",
            (ctx.guild.id, channel.id))
        self.bot.database.commit()
        embed = discord.Embed(title="Set Alert Channel", description=f"Set alert channel to {channel.mention}",
                              color=0x00ff00)
        embed.timestamp = datetime.datetime.now()
        await ctx.send(embed=embed)
        self.bot.loop.create_task(self.plex_alerts(ctx.guild.id, channel.id))

    @has_permissions(administrator=True)
    @command(name='set_plex_server', aliases=['setplexserver', 'sp'])
    async def set_plex_server(self, ctx, plex_url: str, plex_token: str):
        """Sets the plex server to use for the bot"""
        if not plex_url.startswith("http"):
            raise BadArgument("Invalid plex url, must be http://<ip>:<port>")
        # Update the plex server in the database with the new values if it exists or create a new entry if it doesn't
        self.bot.database.execute('''INSERT OR REPLACE INTO plex_servers (guild_id, server_url, server_token) VALUES (?, 
        ?, ?)''', (ctx.guild.id, plex_url, plex_token))
        self.bot.database.commit()
        embed = discord.Embed(title="Set Plex Server", description=f"Set plex server to {plex_url}",
                              color=0x00ff00)
        embed.timestamp = datetime.datetime.now()
        await ctx.send(embed=embed)
        # Delete the command message as it contains the servers token
        await ctx.message.delete()

    @command(name='transcoding', aliases=['layer8', 'thespiel', 'therant', 'pebkac'], description="Just ask nick")
    async def transcoding(self, ctx):
        spiel_txt = """```Some video files are encoded in a way that the Plex player will lag or drop frames while 
        watching. For the best experience, follow these steps: 1. Install the plex windows app, DO NOT use the 
        website version. (!download) 2. Click the settings icon in the top right of the Plex player 3. Under the 
        "Plex for Windows" section, go to "Player" 4. Click the "Show Advanced" button in the top right 5. Under 
        "Video", uncheck the box that reads "Use Hardware Decoding" This should fix the vast majority of playback 
        issues. ``` """
        await ctx.send(spiel_txt)

    @has_permissions(manage_guild=True)
    @command(name="run_butler_task", aliases=["rbt"])
    async def run_butler_task(self, ctx, *, task_name: str):
        """Runs a butler task"""
        available_tasks = [task.name for task in ctx.plex.butlerTasks()]
        if task_name not in available_tasks:
            raise BadArgument("Task not found")
        ctx.plex.runButlerTask(task_name)

    @has_permissions(manage_guild=True)
    @command(name="deep_media_analysis", aliases=["dma"])
    async def deep_media_analysis(self, ctx):
        """Force the plex server to run a deep media analysis"""
        ctx.plex.runButlerTask("DeepMediaAnalysis")
        await ctx.send("Deep media analysis started")

    def event_error(self, error):
        print(error)

    async def event_message_loop(self, plex, queue, channel):
        self.event_tracker[channel.guild.id] = []
        lock = asyncio.Lock()
        while True:
            try:
                event = await queue.get()
                if event is None:
                    break
                print(event)
                await lock.acquire()  # Ensure only one event is processed at a time
                await self.send_event_message(plex, channel, event)
                lock.release()
                await asyncio.sleep(1)
            except Exception as e:
                logging.error(e)
                logging.exception(e)

    class PlexMediaEvent:

        def __init__(self, itemID, message):
            self.message = message
            self.itemID = itemID

            self.title = "This media has no title and this should never be matched"

        def __eq__(self, other):
            return other['itemID'] == self.itemID or other['title'] == self.title

    def get_media_event(self, guild_id, itemID, title=None):
        for event in self.event_tracker[guild_id]:
            if event.itemID == itemID or event.title == title:
                return event
        return None

    async def send_event_message(self, plex, channel, event):
        embed = discord.Embed()
        if int(event['sectionID']) == -1:
            return
        # only include messages with an ID of 0, 5, 9
        if event['state'] == 0:
            embed.title = "New Media Added"
            embed.color = 0x00ff00
            library = plex.library.sectionByID(int(event['sectionID']))
            embed.description = f"New media file added to {library.title}"
            embed.set_footer(text=f"Waiting for item matching to complete")
            msg = await channel.send(embed=embed)
            self.event_tracker[channel.guild.id].append(self.PlexMediaEvent(event['itemID'], msg))
        elif event['state'] == 1:  # Matching item
            if event in self.event_tracker[channel.guild.id]:
                event_obj = self.get_media_event(channel.guild.id, event['itemID'])
                event_obj.title = event['title']
                embed.title = "New Media Added"
                embed.color = 0x00ff00
                library = plex.library.sectionByID(int(event['sectionID']))
                embed.description = f"Media `{event['title']}` added to `{library.title}`"
                embed.set_footer(text=f"Waiting for metadata download to start, media ID: {event['itemID']}")
                await event_obj.message.edit(embed=embed)
        elif event['state'] == 3:  # metadata download started
            if event in self.event_tracker[channel.guild.id]:
                event_obj = self.get_media_event(channel.guild.id, event['itemID'], event['title'])
                event_obj.itemID = event['itemID']
                embed.title = "New Media Added"
                embed.color = 0x00ff00
                library = plex.library.sectionByID(int(event['sectionID']))
                embed.description = f"Media `{event['title']}` added to `{library.title}`"
                embed.set_footer(text=f"Waiting for metadata download to finish, media ID: {event['itemID']}")
                msg = self.get_media_event(channel.guild.id, event['itemID']).message
                await msg.edit(embed=embed)
        elif event['state'] == 5:
            if event in self.event_tracker[channel.guild.id]:
                library = plex.library.sectionByID(int(event['sectionID']))
                media = get_from_media_index(library, event['itemID'])
                if media is not None:
                    if media.isPartialObject():  # For some reason plex likes to not give everything we asked for
                        media.reload()
                    if isinstance(media, plexapi.video.Movie):
                        embed.title = safe_field(media.title)
                    elif isinstance(media, plexapi.video.Episode):
                        embed.title = f"{media.grandparentTitle}\n{media.title} " \
                                      f"(S{media.parentIndex}E{media.index})"
                    base_info_layer(embed, media)
                    embed.color = 0x00ff00
                    embed.set_footer(text=f"Located in {library.title}, Plex ID: {media.ratingKey}")
                msg = self.get_media_event(channel.guild.id, event['itemID']).message
                await msg.edit(content="Media Added", embed=embed)
        elif event['state'] == 9:  # Media deleted
            if event in self.event_tracker[channel.guild.id]:
                msg = self.get_media_event(channel.guild.id, event['itemID']).message
                title = self.get_media_event(channel.guild.id, event['itemID']).title
                embed.title = "New Media Added"
                embed.color = 0x00ff00
                library = plex.library.sectionByID(int(event['sectionID']))
                embed.description = f"Media `{title}` deleted from `{library.title}`"
                embed.set_footer(text=f"Media is being merged into another item, media ID: {event['itemID']}")
                await msg.edit(embed=embed)
                return

            library = plex.library.sectionByID(int(event['sectionID']))
            embed.title = "Media Deleted"
            embed.color = 0xff0000
            embed.description = f"Media {event['title']} deleted from {library.title}"
            embed.set_footer(text=f"Media deleted")
            await channel.send(embed=embed)

    @has_permissions(manage_guild=True)
    @command(name="start_event_listener", aliases=["sel"])
    async def start_event_listener(self, ctx):
        """Starts the event listener"""
        event_queue = asyncio.Queue()

        def event_callback(data):
            if data['type'] == 'timeline':
                entry = data['TimelineEntry'][0]
                if entry['identifier'] == 'com.plexapp.plugins.library':
                    event_queue.put_nowait(entry)

        listener = plexapi.alert.AlertListener(ctx.plex, event_callback, self.event_error)
        listener.start()
        self.bot.loop.create_task(self.event_message_loop(ctx.plex, event_queue, ctx.channel))
        logging.info("Started event listener")
        while listener.is_alive():
            await asyncio.sleep(1)
        logging.info("Event listener stopped")


async def setup(bot):
    await bot.add_cog(PlexBot(bot))
    logging.info("PlexBot loaded successfully")
