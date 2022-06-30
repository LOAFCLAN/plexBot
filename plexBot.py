import asyncio
import datetime
import random
import traceback
import typing

from discord.ext.commands import command, has_permissions, Cog, Context
from plexapi.server import PlexServer
import discord.errors as discord_errors
import discord

from main import session_embed, get_all_library


class PlexBot(Cog):

    def dynamic_plex(self, context: typing.Union[Context, int]):
        if isinstance(context, Context):
            guild_id = context.guild.id
        else:
            guild_id = context

        if guild_id not in self.plex_servers:
            cursor = self.bot.database.execute("SELECT * FROM plex_servers WHERE guild_id = ?", (guild_id,))
            if cursor.rowcount == 0:
                raise Exception("No plex server found for this guild")
            row = cursor.fetchone()
            try:
                self.plex_servers[guild_id] = PlexServer(row[1], row[2])
            except Exception:
                raise Exception("Invalid plex server credentials, or server is offline")
        return self.plex_servers[guild_id]

    async def user_add(self, ctx, plex_id):
        plex = self.dynamic_plex(ctx)
        celery = plex.myPlexAccount()
        pending = celery.pendingInvites()
        if len(pending) == 0:
            embed = discord.Embed(title="Add User", description="There are currently no pending invites, "
                                                                "user was not added", color=0xFF0000)
            embed.timestamp = datetime.datetime.utcnow()
            await ctx.send(embed=embed)
            return
        for invite in pending:
            if invite.username == plex_id:
                print(invite.__dict__)
                embed = discord.Embed(title="Add User", description=f"User `{invite.username}` was added",
                                      thumbnail=f"{invite.thumb}.png", color=0x00ff00)
                celery.acceptInvite(invite.username)
                celery.inviteFriend(invite.email, plex, get_all_library(plex))
                movie_library_string = ""
                for library in get_all_library(plex):
                    if library.type == "movie":
                        movie_library_string += f"`{library.title}` (Size: `{library.totalSize}`)\n"
                embed.add_field(name="Movie Library's", value=movie_library_string, inline=True)
                show_library_string = ""
                for library in get_all_library(plex):
                    if library.type == "show":
                        show_library_string += f"`{library.title}` (Size: `{library.totalSize}`)\n"
                embed.add_field(name="Show Library's", value=show_library_string, inline=True)
                embed.timestamp = datetime.datetime.utcnow()
                await ctx.send(embed=embed)
                return plex.getUser(plex_id)

        embed = discord.Embed(title="Add User", description="User was not found in pending invites", color=0xFF0000)
        embed.timestamp = datetime.datetime.utcnow()
        await ctx.send(embed=embed)

    def __init__(self, bot):
        self.bot = bot
        self.plex_servers = {}
        cursor = self.bot.database.execute("SELECT * FROM activity_messages")
        self.activity_messages = [row for row in cursor.fetchall()]

    @Cog.listener('on_ready')
    async def on_ready(self):
        print(f"plexBot cog is ready")
        for message_config in self.activity_messages:
            self.bot.loop.create_task(self.monitor_plex(message_config[0], message_config[1], message_config[2]))

    async def monitor_plex(self, guild_id: int, channel_id: int, message_id: int):
        try:
            plex = self.dynamic_plex(guild_id)
            channel = await self.bot.fetch_channel(channel_id)

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
                    await message.edit(embed=embed, content=None)
                    # await log_scan()
                    await asyncio.sleep(5)
                except discord_errors.NotFound:
                    message = await create_message()
                except Exception as e:
                    print(e)
                    traceback.print_exc()
                    embed = discord.Embed(title="Plex Monitor",
                                          description=f"{self.bot.name} has encountered an error", color=0xFF0000)
                    embed.add_field(name="Error", value=f"{e}", inline=False)
                    embed.add_field(name="Traceback", value=traceback.format_exc(), inline=False)
                    embed.timestamp = datetime.datetime.utcnow()
                    await message.edit(embed=embed)
                    await asyncio.sleep(5)
        except Exception as e:
            print(e)
            traceback.print_exc()
            await asyncio.sleep(5)
            self.bot.loop.create_task(self.monitor_plex(guild_id, channel_id, message_id))


    @command(name="pending_invites", aliases=["pendinginvites", "pendinginvite", "pending"])
    async def pending_invites(self, ctx):
        plex = self.dynamic_plex(ctx)
        celery = plex.myPlexAccount()
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

        embed.timestamp = datetime.datetime.utcnow()
        await ctx.send(embed=embed)

    @command(name="invite_friend", aliases=["invite", "invite_user"])
    async def invite_friend(self, ctx, *, plex_id: str):
        plex = self.dynamic_plex(ctx)
        celery = plex.myPlexAccount()
        celery.inviteFriend(plex_id, plex, get_all_library(plex))
        movie_library_string = ""
        for library in get_all_library(plex):
            if library.type == "movie":
                movie_library_string += f"`{library.title}` (Size: `{library.totalSize}`)\n"
        embed = discord.Embed(title="Invite Friend", description="Friend was invited", color=0x00ff00)
        embed.add_field(name="Movie Library's", value=movie_library_string, inline=True)
        show_library_string = ""
        for library in get_all_library(plex):
            if library.type == "show":
                show_library_string += f"`{library.title}` (Size: `{library.totalSize}`)\n"
        embed.add_field(name="Show Library's", value=show_library_string, inline=True)
        embed.timestamp = datetime.datetime.utcnow()
        await ctx.send(embed=embed)

    # @has_permissions(manage_guild=True)
    @command(name="accept_user", aliases=["accept", "au"])
    async def accept_user(self, ctx, user_id):
        await self.user_add(ctx, user_id)

    @has_permissions(administrator=True)
    @command(name="remove_user", aliases=["removeuser", "ru"])
    async def remove_user(self, ctx, user_id):
        plex = self.dynamic_plex(ctx)
        celery = plex.myPlexAccount()
        users = plex.systemAccounts()
        for user in users[2:]:
            if user.name == user_id:
                embed = discord.Embed(title="Remove User",
                                      description=f"Are you sure you want to remove `{user.name}`?", color=0xffff00)
                embed.set_footer(text="React with ✅ to confirm, or ❎ to cancel")
                msg = await ctx.send(embed=embed)
                await msg.add_reaction("✅")
                await msg.add_reaction("❎")

                def check(msg_reaction, msg_user):
                    return msg_user == ctx.author and str(msg_reaction.emoji) in ["✅", "❎"]

                try:
                    reaction, msg_user = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
                    if str(reaction.emoji) == "✅":
                        try:
                            celery.removeFriend(user.id)
                            embed = discord.Embed(title="Remove User",
                                                  description=f"User `{user.name}` was removed from {plex.friendlyName}",
                                                  color=0x00ff00)
                            embed.timestamp = datetime.datetime.utcnow()
                            await msg.edit(embed=embed)
                        except Exception as e:
                            embed = discord.Embed(title="Remove User",
                                                  description=f"User `{user.name}` was not removed from {plex.friendlyName}",
                                                  color=0xFF0000)
                            embed.add_field(name="Error", value=f"{e}", inline=False)
                            embed.timestamp = datetime.datetime.utcnow()
                            await msg.edit(embed=embed)
                    elif str(reaction.emoji) == "❎":
                        embed = discord.Embed(title="Remove User",
                                              description=f"User `{user.name}` was not removed from {plex.friendlyName}"
                                              , color=0xFF0000)
                        embed.timestamp = datetime.datetime.utcnow()
                        await msg.edit(embed=embed)
                except asyncio.TimeoutError:
                    embed = discord.Embed(title="Remove User",
                                          description="Timed out waiting for confirmation", color=0xFF0000)
                    embed.timestamp = datetime.datetime.utcnow()
                    await ctx.send(embed=embed)
                    return

    @has_permissions(manage_guild=True)
    @command(name='users')
    async def users(self, ctx):
        plex = self.dynamic_plex(ctx)
        celery = plex.myPlexAccount()
        users = celery.users()
        embed = discord.Embed(title="Users", description="", color=0x00ff00)
        for user in users:
            username = user.username
            email = user.email
            if username is None or len(username) <= 0:
                username = "N/A"
            if email is None or len(email) <= 0:
                email = "N/A"
            embed.add_field(name=f"{username}", value=f"{email}", inline=False)
        embed.timestamp = datetime.datetime.utcnow()
        await ctx.send(embed=embed, delete_after=30)

    @command(name='ping')
    async def ping(self, ctx):
        ping_responses = ["Pong!", "What's up", "I'm here!", "I'm here, I'm here!",
                          "I'm here, I'm here, I'm here!"]
        await ctx.send(random.choice(ping_responses))

    @command(name='signup', aliases=['register'])
    async def signup(self, ctx):
        await ctx.send("https://plex.tv/sign-up")

    @command(name='sessions')
    async def sessions(self, ctx):
        plex = self.dynamic_plex(ctx)
        embed = await session_embed(plex)
        await ctx.send(embed=embed)

    @has_permissions(manage_guild=True)
    @command(name='plex')
    async def plex_status(self, ctx):
        plex = self.dynamic_plex(ctx)
        embed = discord.Embed(title="Plex Status", description="", color=0x00ff00)
        embed.add_field(name="Clients", value=f"{len(plex.systemAccounts())}", inline=False)
        for client in plex.systemAccounts():
            if len(client.name) < 1:
                client.name = "Unknown"
            embed.add_field(name=client.name, value=client.key, inline=False)

        await ctx.send(embed=embed)

    @has_permissions(administrator=True)
    @command(name='set_activity_channel', aliases=['setactivitychannel', 'sac'])
    async def set_activity_channel(self, ctx, channel: discord.TextChannel):
        """Adds a plex activity channel to the database"""
        self.bot.database.execute(
            "INSERT INTO activity_messages (guild_id, channel_id, message_id) VALUES (?, ?, ?)",
            (ctx.guild.id, channel.id, 0))
        self.bot.database.commit()
        embed = discord.Embed(title="Set Activity Channel", description=f"Set activity channel to {channel.mention}",
                              color=0x00ff00)
        embed.timestamp = datetime.datetime.utcnow()
        await ctx.send(embed=embed)
        self.bot.loop.create_task(self.monitor_plex(ctx.guild.id, channel.id, 0))

    @has_permissions(administrator=True)
    @command(name='set_plex_server', aliases=['setplexserver', 'sp'])
    async def set_plex_server(self, ctx, plex_url: str, plex_token: str):
        """Sets the plex server to use for the bot"""
        # Update the plex server in the database with the new values if it exists or create a new entry if it doesn't
        self.bot.database.execute('''INSERT OR REPLACE INTO plex_servers (guild_id, server_url, server_token) VALUES (?, 
        ?, ?)''', (ctx.guild.id, plex_url, plex_token))
        self.bot.database.commit()
        embed = discord.Embed(title="Set Plex Server", description=f"Set plex server to {plex_url}",
                              color=0x00ff00)
        embed.timestamp = datetime.datetime.utcnow()
        await ctx.send(embed=embed)
        # Delete the command message as it contains the servers token
        await ctx.message.delete()


def setup(bot):
    bot.add_cog(PlexBot(bot))
    print("Plex cog loaded")