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

from wrappers_utils.Modals import ReviewModal


class PlexSearchView(View):

    def __init__(self, user, plex_search, mode: str, results: typing.List, current_content=None):
        super().__init__(timeout=60)
        self.target_message = None
        self.user = user
        self.mode = mode
        self.plex_search = plex_search
        self.results = results
        self.client = self.plex_search.bot
        self.current_content = current_content
        if mode != "content":
            self.generate_search_select()
        if mode != "search":
            row = self.client.database.get_table("plex_watched_media").get_row(guild_id=user.guild.id,
                                                                               media_guid=self.current_content.guid)
            if row:
                self.add_item(Button(label="Add Review", style=ButtonStyle.grey, custom_id="add_review", emoji="📝"))
            else:
                self.add_item(Button(label="Add Review", style=ButtonStyle.grey, custom_id="add_review", emoji="📝",
                                     disabled=True))
        self.add_item(Button(label="Cancel", style=ButtonStyle.red, custom_id="cancel"))
        self.timeout_task = self.client.loop.create_task(self.timeout_task())

    async def timeout_task(self):
        """After 5 minutes, the view will timeout and disable all buttons"""
        try:
            await self.wait()
            # Set all items to disabled, but don't remove them
            for item in self.children:
                item.disabled = True
            await self.target_message.edit(view=self)
        except Exception as e:
            logging.error(f"Error in timeout task: {e}")
            logging.exception(e)

    def set_message(self, message):
        self.target_message = message

    async def interaction_check(self, interaction: Interaction):
        if interaction.user.id == self.user.id:
            await self.process_interaction(interaction)
        else:
            await interaction.response.send_message("You cannot use this menu.", ephemeral=True)
            return False

    async def process_interaction(self, interaction: Interaction):
        if interaction.data["custom_id"] == "cancel":
            await interaction.response.defer()
            self.stop()
        elif interaction.data["custom_id"] == "add_review":

            row = self.client.database.get_table("plex_watched_media").get_row(guild_id=interaction.guild.id,
                                                                               media_guid=self.current_content.guid)
            if row:
                await interaction.response.send_modal(ReviewModal(row["media_id"]))
            else:
                await interaction.response.send_message("This media has not been watched yet", ephemeral=True)
        else:
            await interaction.response.defer()
            await interaction.message.edit(content="Searching...", embed=None, view=None)
            await self.plex_search.content_details(interaction.message,
                                                   self.results[int(interaction.data["values"][0])],
                                                   interaction.user)

    def generate_search_select(self):
        select_thing = Select(custom_id="content_search", placeholder="Select a result", min_values=1, max_values=1)
        # Remove any duplicates
        labels = []
        for result in self.results:
            if result.type == "movie":
                label = f"{result.title} ({result.year})"
            elif result.type == "show":
                label = f"{result.title} ({result.year})"
            elif result.type == "season":
                label = f"Season {result.index}"
            elif result.type == "episode":
                label = f"Episode {result.index} - {result.title}"
            else:
                label = result.title
            if label not in labels:
                labels.append(label)
                if result.type == "season":
                    select_thing.add_option(label=label, value=str(self.results.index(result)),
                                            description=f"Episodes: {len(result.episodes())}")
                else:
                    select_thing.add_option(label=label, value=str(self.results.index(result)))
            else:
                logging.info(f"Duplicate result found: {label}")
        self.add_item(select_thing)


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
            msg = await ctx.send("Found 1 result. Showing details...")
            await self.content_details(msg, results[0], ctx.author)
            return
        else:
            embed = discord.Embed(title="Search results for '%s'" % query, color=0x00ff00)
            for result in results:
                embed.add_field(name=f"{result.title} ({result.year})", value=safe_field(result.summary[:1024]),
                                inline=False)
            view = PlexSearchView(ctx.author, self, "search", results)
            view.set_message(await ctx.send(embed=embed, view=view))

    async def content_details(self, edit_msg, content, requester):
        """Show details about a content"""
        view = None

        if content.isPartialObject():  # For some reason plex likes to not give everything we asked for
            content.reload()  # So if plex is being a jerk, we'll reload the content

        if isinstance(content, plexapi.video.Movie):
            """Format the embed being sent for a movie"""
            embed = discord.Embed(title=f"{content.title} ({content.year})",
                                  description=f"{content.tagline if content.tagline else 'No Tagline'}", color=0x00ff00)
            embed.add_field(name="Summary", value=content.summary, inline=False)

            base_info_layer(embed, content, database=self.bot.database)
            view = PlexSearchView(requester, self, "content", content, content)

        elif isinstance(content, plexapi.video.Show):  # ----------------------------------------------------------
            """Format the embed being sent for a show"""

            rating_string = rating_str(content, database=self.bot.database)

            embed = discord.Embed(title=f"{safe_field(content.title)}",
                                  description=f"{content.tagline if content.tagline else 'No Tagline'}", color=0x00ff00)
            embed.add_field(name="Summary", value=safe_field(content.summary), inline=False)
            embed.add_field(name="Rating", value=rating_string, inline=False)
            embed.add_field(name="Genres", value=stringify(content.genres), inline=False)

            embed.add_field(name="Studio", value=content.studio, inline=True)
            embed.add_field(name="Network", value=content.network, inline=True)
            embed.add_field(name="Originally Aired", value=content.originallyAvailableAt.strftime("%B %d, %Y"),
                            inline=True)

            embed.add_field(name="Average Episode Runtime",
                            value=f"{datetime.timedelta(milliseconds=content.duration)}", inline=True)
            embed.add_field(name="Total Duration",
                            value=f"{datetime.timedelta(seconds=round(get_series_duration(content) / 1000))}",
                            inline=True)
            embed.add_field(name="Watch Time", value=f"{get_watch_time(content, self.bot.database)}", inline=True)
            embed.add_field(name="Total Season", value=content.childCount, inline=True)
            embed.add_field(name="Total Episodes", value=f"{len(content.episodes())}", inline=True)
            embed.add_field(name="Total Sessions", value=f"{get_session_count(content, self.bot.database)}",
                            inline=True)
            view = PlexSearchView(requester, self, "season", content.seasons(), content)

        elif isinstance(content, plexapi.video.Season):  # ------------------------------------------------------
            """Format the embed being sent for a season"""
            embed = discord.Embed(title=f"{content.parentTitle}",
                                  description=f"Season {content.index}", color=0x00ff00)
            embed.add_field(name=f"Episodes: {len(content.episodes())}",
                            value=stringify(content.episodes(), separator="\n")[:1024], inline=False)
            embed.add_field(name="Total Duration",
                            value=f"{datetime.timedelta(seconds=round(get_series_duration(content) / 1000))}",
                            inline=True)
            view = PlexSearchView(requester, self, "episode", content.episodes(), content)

        elif isinstance(content, plexapi.video.Episode):  # ------------------------------------------------------
            """Format the embed being sent for an episode"""
            embed = discord.Embed(title=f"{content.grandparentTitle}\n{content.title} "
                                        f"(S{content.parentIndex}E{content.index})",
                                  description=f"{content.summary}", color=0x00ff00)
            base_info_layer(embed, content, database=self.bot.database)
            view = PlexSearchView(requester, self, "content", content, content)
        else:
            embed = discord.Embed(title="Unknown content type", color=0x00ff00)

        ###############################################################################################################

        db_entry = self.bot.database.get_table("plex_watched_media").get_row(media_guid=content.guid)

        # if inter is not None:
        #     await inter.disable_components()

        if hasattr(content, "thumb"):
            thumb_url = cleanup_url(content.thumb)
            embed.set_thumbnail(url=thumb_url)

        # embed.set_footer(text=f"{content.guid}", icon_url=requester.avatar_url)
        embed.set_author(name=f"Requested by: {requester.display_name}", icon_url=requester.display_avatar.url)

        embed.set_footer(text=f"Located in {content.librarySectionTitle}, "
                              f"Media ID: {db_entry['media_id'] if db_entry else 'N/A'}")

        if view:
            view.set_message(edit_msg)
            await edit_msg.edit(embed=embed, view=view)
        else:
            await edit_msg.edit(embed=embed, view=None)

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
