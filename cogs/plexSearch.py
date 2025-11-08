import datetime
import typing

import discord
import humanize as humanize
import plexapi.base
import plexapi.video
from discord import Interaction, ButtonStyle
from discord.ext import commands
from discord.ext.commands import command
# from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction

from discord.ui import Button, View, Select

from utils import get_season, base_info_layer, rating_str, stringify, make_season_selector, make_episode_selector, \
    cleanup_url, safe_field, get_series_duration, get_watch_time, get_session_count

from loguru import logger as logging

from wrappers_utils.MediaEmbedifier import media_details, PlexSearchView
from wrappers_utils.Modals import ReviewModal


class PlexSearch(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.content_cache = {}

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

    @commands.hybrid_command(name="content_search", aliases=["cs"])
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
            msg = await ctx.send("Found 1 result. Loading details...")
            embed, view = await media_details(results[0], self=self, ctx=ctx, requester=ctx.author)
            await msg.edit(content=None, embed=embed, view=view)
            view.set_message(msg)
            return
        else:
            embed = discord.Embed(title="Search results for '%s'" % query, color=0x00ff00)
            for result in results:
                embed.add_field(name=f"{result.title} ({result.year})", value=safe_field(result.summary[:1024]),
                                inline=False)
            view = PlexSearchView(ctx.author, self, ctx, "search", results)
            view.set_message(await ctx.send(embed=embed, view=view))

    async def on_timeout(self, view):
        """Called when a button times out"""
        # remove the view
        await view.message.edit(view=None, embed=view.message.embeds[0])

    @command(name="library", aliases=["lib", "libraries"], description="List all libraries")
    async def library_list(self, ctx):
        pass


async def setup(bot):
    await bot.add_cog(PlexSearch(bot))
    logging.info("PlexSearch loaded successfully")
