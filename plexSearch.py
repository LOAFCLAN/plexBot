import datetime
import typing

import discord
import humanize as humanize
import plexapi.base
import plexapi.video
from discord import Interaction
from discord.ext import commands
from discord.ext.commands import command
# from discord_components import DiscordComponents, Button, ButtonStyle, SelectOption, Select, Interaction

from discord.ui import Button, View, Select

from utils import get_season, base_info_layer, rating_str, stringify, make_season_selector, make_episode_selector, \
    cleanup_url, safe_field, get_series_duration

from loguru import logger as logging

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
                embed.add_field(name=f"{result.title} ({result.year})", value=safe_field(result.summary[:1024]),
                                inline=False)
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
                                  description=f"{content.tagline if content.tagline else 'No Tagline'}", color=0x00ff00)
            embed.add_field(name="Summary", value=content.summary, inline=False)

            base_info_layer(embed, content)

        elif isinstance(content, plexapi.video.Show):  # ----------------------------------------------------------
            """Format the embed being sent for a show"""

            rating_string = rating_str(content)

            embed = discord.Embed(title=f"{safe_field(content.title)}",
                                  description=f"{content.tagline if content.tagline else 'No Tagline'}", color=0x00ff00)
            embed.add_field(name="Summary", value=safe_field(content.summary), inline=False)
            embed.add_field(name="Rating", value=rating_string, inline=False)
            embed.add_field(name="Genres", value=stringify(content.genres), inline=False)
            embed.add_field(name="Network", value=content.network, inline=True)
            embed.add_field(name="Studio", value=content.studio, inline=True)
            embed.add_field(name="Average Episode Runtime",
                            value=f"{datetime.timedelta(milliseconds=content.duration)}", inline=True)
            embed.add_field(name="Total Seasons", value=content.childCount, inline=True)
            embed.add_field(name="Total Episodes", value=f"{len(content.episodes())}", inline=True)
            embed.add_field(name="Total Duration",
                            value=f"{datetime.timedelta(seconds=round(get_series_duration(content) / 1000))}", inline=True)
            # embed.add_field(name="Media", value="\n".join(media_info), inline=False)
            select_things = make_season_selector(content)
            for thing in select_things:
                self.bot.component_manager.add_callback(thing, self.on_select)

        elif isinstance(content, plexapi.video.Season):  # ------------------------------------------------------
            """Format the embed being sent for a season"""
            embed = discord.Embed(title=f"{content.parentTitle}",
                                  description=f"Season {content.index}", color=0x00ff00)
            embed.add_field(name=f"Episodes: {len(content.episodes())}",
                            value=stringify(content.episodes(), separator="\n")[:1024], inline=False)
            embed.add_field(name="Total Duration",
                            value=f"{datetime.timedelta(seconds=round(get_series_duration(content) / 1000))}",
                            inline=True)
            select_things = make_episode_selector(content)
            for thing in select_things:
                self.bot.component_manager.add_callback(thing, self.on_select)

        elif isinstance(content, plexapi.video.Episode):  # ------------------------------------------------------
            """Format the embed being sent for an episode"""
            embed = discord.Embed(title=f"{content.grandparentTitle}\n{content.title} "
                                        f"(S{content.parentIndex}E{content.index})",
                                  description=f"{content.summary}", color=0x00ff00)
            base_info_layer(embed, content)

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


async def setup(bot):
    await bot.add_cog(PlexSearch(bot))
    logging.info("PlexSearch loaded successfully")