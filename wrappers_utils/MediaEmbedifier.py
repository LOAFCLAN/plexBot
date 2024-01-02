import datetime
import typing

import discord
import humanize
import plexapi
from discord import ButtonStyle, Interaction
from discord.ui import View, Button, Select

from utils import stringify, get_series_duration, base_info_layer, get_watch_time, get_session_count, safe_field, \
    rating_str, cleanup_url, get_series_size
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
            await interaction.message.edit(content=f"Loading... `{self.results[int(interaction.data['values'][0])]}`",
                                           embed=None, view=None)
            embed, view = await media_details(content=self.results[int(interaction.data["values"][0])],
                                              self=self.plex_search,
                                              requester=interaction.user)
            # Set the target message to the new message
            msg = await interaction.message.edit(content=None, embed=embed, view=view)
            view.set_message(msg)

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


async def media_details(content, self=None, requester=None, full=True):
    """Show details about a content"""
    view = None

    if content.isPartialObject():  # For some reason plex likes to not give everything we asked for
        content.reload()  # So if plex is being a jerk, we'll reload the content

    if isinstance(content, plexapi.video.Movie):
        """Format the embed being sent for a movie"""
        embed = discord.Embed(title=f"{content.title} ({content.year})",
                              description=f"{content.tagline if content.tagline else 'No Tagline'}", color=0x00ff00)
        if full:
            embed.add_field(name="Summary", value=content.summary, inline=False)

        base_info_layer(embed, content, database=self.bot.database, full=full)
        if self and requester:
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
        size = await self.bot.loop.run_in_executor(None, get_series_size, content)
        embed.add_field(name="Size", value=humanize.naturalsize(size), inline=True)
        embed.add_field(name="Originally Aired", value=content.originallyAvailableAt.strftime("%B %d, %Y"),
                        inline=True)

        embed.add_field(name="Average Episode Runtime",
                        value=f"{datetime.timedelta(milliseconds=content.duration)}", inline=True)
        embed.add_field(name="Total Duration",
                        value=f"{datetime.timedelta(seconds=round(get_series_duration(content) / 1000))}",
                        inline=True)
        embed.add_field(name="Watch Time", value=f"{get_watch_time(content, self.bot.database)}", inline=True)
        embed.add_field(name="Total Seasons", value=content.childCount, inline=True)
        embed.add_field(name="Total Episodes", value=f"{len(content.episodes())}", inline=True)
        embed.add_field(name="Total Sessions", value=f"{get_session_count(content, self.bot.database)}",
                        inline=True)
        if self and requester:
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
        if self and requester:
            view = PlexSearchView(requester, self, "episode", content.episodes(), content)

    elif isinstance(content, plexapi.video.Episode):  # ------------------------------------------------------
        """Format the embed being sent for an episode"""
        embed = discord.Embed(title=f"{content.grandparentTitle}\n{content.title} "
                                    f"(S{content.parentIndex}E{content.index})",
                              description=f"{content.summary}" if full else "", color=0x00ff00)
        base_info_layer(embed, content, database=self.bot.database, full=full)
        if self and requester:
            view = PlexSearchView(requester, self, "content", content, content)
    else:
        embed = discord.Embed(title="Unknown content type", color=0x00ff00)

    ###############################################################################################################

    db_entry = self.bot.database.get_table("plex_watched_media").get_row(media_guid=content.guid)

    # if inter is not None:
    #     await inter.disable_components()

    if hasattr(content, "bad_thumb"):
        embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1191806535861538948/1191806693621911572/bad_thumb.png")
    elif hasattr(content, "thumb"):
        thumb_url = cleanup_url(content.thumb)
        embed.set_thumbnail(url=thumb_url)

    # embed.set_footer(text=f"{content.guid}", icon_url=requester.avatar_url)
    if requester:
        embed.set_author(name=f"Requested by: {requester.display_name}", icon_url=requester.display_avatar.url)

    embed.set_footer(text=f"Located in {content.librarySectionTitle}, "
                          f"Media ID: {db_entry['media_id'] if db_entry else 'N/A'}, "
                          f"Plex ID: {content.ratingKey}")

    return embed, view
