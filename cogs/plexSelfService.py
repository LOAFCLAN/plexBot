import asyncio
import os
from encodings.aliases import aliases
from typing import Optional

import discord
import humanize
import qbittorrentapi


from ConcurrentDatabase.Database import Database
from discord.ext.commands import command, has_permissions, Cog, hybrid_command
from discord import app_commands
from discord.ui import View, Button, Select
from loguru import logger as logging

from watfag.search import Jackett
from watfag.parsers.generic import SeedStatus
from watfag.parsers.movie import MovieRelease
from watfag.parsers.tvboxset import TVBoxSetRelease

class PlexSelfService(Cog):

    def __init__(self, bot):
        self.bot = bot
        self.init_database()
        self.jackett_instances = {}
        self.torrent_cache = {}
        jackett_table = self.bot.database.get_table("jackett_servers")
        for row in jackett_table.get_all():
            try:
                self.jackett_instances[row["guild_id"]] = Jackett(api_key=row["api_key"], base_url=row["host"])
            except Exception as e:
                logging.error(f"Error initializing Jackett instance for guild {row['guild_id']}: {e}")
                logging.exception(e)

    def init_database(self):
        self.bot.database.create_table("qbittorrent_servers", {"guild_id": "INTEGER PRIMARY KEY", "host": "TEXT",
                                                               "port": "INTEGER", "username": "TEXT", "password": "TEXT",
                                                                "default_movie_library": "TEXT", "default_tv_library": "TEXT"})
        self.bot.database.create_table("jackett_servers",
                                       {"guild_id": "INTEGER PRIMARY KEY", "host": "TEXT", "api_key": "TEXT"})

    def get_qbittorrent(self, guild_id):
        table = self.bot.database.get_table("qbittorrent_servers")
        qbittorrent = table.get_row(guild_id=guild_id)
        if qbittorrent is None:
            return None
        try:
            return qbittorrentapi.Client(host=qbittorrent["host"], port=qbittorrent["port"],
                                         username=qbittorrent["username"], password=qbittorrent["password"])
        except Exception as e:
            logging.exception(e)
            raise ConnectionError("Unable to connect to qbittorrent")

    async def search_jackett(self, guild_id, query):
        jackett = self.jackett_instances.get(guild_id, None)
        if jackett is None:
            raise ConnectionError("Jackett not configured for this guild")
        return await jackett.search(query)


    @staticmethod
    def movie_comparator(result, torrent_entry):
        logging.info(f"Comparing movie `{result.title}` with `{torrent_entry.title}`")
        if hasattr(result, "year") and hasattr(torrent_entry, "year") and result.year == torrent_entry.year:
            return result
        if torrent_entry.title.lower() in result.title.lower() or result.title.lower() in torrent_entry.title.lower():
            return result
        return None

    @staticmethod
    def tv_comparator(result, torrent_entry):
        logging.info(f"Comparing show `{result.title}` with `{torrent_entry.title}`")
        if result.isPartialObject():  # For some reason plex likes to not give everything we asked for
            result.reload()
        seasons = []
        if hasattr(torrent_entry, "seasons") and torrent_entry.seasons is not None:
            if len(torrent_entry.seasons.split("-")) > 1:
                seasons = list(range(int(torrent_entry.seasons.split("-")[0]), int(torrent_entry.seasons.split("-")[1]) + 1))
            else:
                seasons = [int(torrent_entry.seasons)]

        if not (torrent_entry.title.lower() in result.title.lower() or
                result.title.lower() in torrent_entry.title.lower()):
            return None
        for season in result.seasons():
            if season.seasonNumber in seasons:
                return result
        return None

    @staticmethod
    def get_media_name(item):
        if item.type == "movie":
            return f"{item.title} ({getattr(item, 'year', 'N/A')})"
        elif item.type == "show":
            return item.title
        elif item.type == "season":
            return f"{item.parentTitle} - Season {item.seasonNumber}"
        elif item.type == "episode":
            return f"{item.grandparentTitle} S{item.parentSeasonNumber}E{item.episodeNumber} - {item.title}"
        else:
            return item.title

    @staticmethod
    def get_media_files(item):
        files = []
        if item.type == "season":  # Find the root directory for all the episodes in the season
            episode_directories = set()
            for episode in item.episodes():
                if hasattr(episode, "media") and episode.media:
                    for media in episode.media:
                        if hasattr(media, "parts") and media.parts:
                            for part in media.parts:
                                if hasattr(part, "file"):
                                    episode_directories.add(os.path.dirname(part.file))
            for directory in episode_directories:
                files.append(os.path.basename(directory))
        elif hasattr(item, "locations"):
            for location in item.locations:
                files.append(os.path.basename(location))
        files = [f"`{file}`" for file in files]
        # Remove duplicates
        files = list(set(files))
        return files

    async def watch_torrent_download(self, release_entry):
        # Send updates to the user on the download status of the torrent until it is complete
        logging.info(f"Watching torrent `{release_entry.original_text}` for download status updates")
        qbittorrent = self.get_qbittorrent(release_entry.message.guild.id)
        try:
            while True:
                torrent_info = qbittorrent.torrents_info(hash=release_entry.torrent_entry.hash)[0]
                print(torrent_info)
                if torrent_info.state != "downloading":
                    embed = discord.Embed(title="Download Complete",
                                          description=f"`{release_entry.original_text}` has finished downloading.",
                                          color=discord.Color.green())
                    await release_entry.message.edit(embed=embed)
                    logging.info(f"Torrent `{release_entry.original_text}` has completed downloading")
                    break
                else:
                    progress = torrent_info.progress
                    embed = discord.Embed(title="Downloading...",
                                          description=f"`{release_entry.original_text}` is currently downloading.",
                                          color=discord.Color.orange())
                    embed.add_field(name="Progress", value=f"{progress:.2f}%", inline=True)
                    embed.add_field(name="Download Speed", value=f"{humanize.naturalsize(torrent_info.dlspeed)}/s", inline=True)
                    embed.add_field(name="Seeders", value=f"{torrent_info.num_seeds}", inline=True)
                    await release_entry.message.edit(embed=embed)
                await asyncio.sleep(30)  # Update every 30 seconds
        except Exception as e:
            logging.exception(e)
            embed = discord.Embed(title="Error Watching Torrent",
                                  description=f"An error occurred while watching the torrent download status: `{e}`",
                                  color=discord.Color.red())
            await release_entry.message.edit(embed=embed)


    async def find_potential_duplicates(self, plex, torrent_entry):
        # Search for the torrent title in the Plex library and return any potential matches
        logging.info(f"Searching for potential duplicates for `{torrent_entry.title}`")
        results = plex.search(torrent_entry.title)
        # Filter the results to only include movies or shows depending on the category of the torrent
        if type(torrent_entry) == MovieRelease:
            results = [r for r in results if r.type == "movie"]
        elif type(torrent_entry) == TVBoxSetRelease:
            results = [r for r in results if r.type == "show"]
        else:
            return []
        logging.info(f"Found {len(results)} potential matches in Plex for `{torrent_entry.title}`")
        # Further filter the results to only include items that have a similar title or year or season (for TV shows)
        potential_duplicates = []
        for result in results:
            if type(torrent_entry) == MovieRelease:
                match = self.movie_comparator(result, torrent_entry)
            elif type(torrent_entry) == TVBoxSetRelease:
                match = self.tv_comparator(result, torrent_entry)
            else:
                continue
            if match is not None:
                potential_duplicates.append(match)
        return potential_duplicates

    async def create_duplicate_warning_embed(self, release, potential_duplicates):

        embed = discord.Embed(title=f"Potential Duplicate{'s' if len(potential_duplicates) > 1 else ''} Found",
                              description=f"Plex already has the following"
                                          f" item{'s' if len(potential_duplicates) > 1 else ''} "
                                          f"that may match\n`{release.original_text.upper()}`",
                              color=discord.Color.orange())
        for item in potential_duplicates:
            if item.isPartialObject():  # For some reason plex likes to not give everything we asked for
                item.reload()
            name = self.get_media_name(item)
            files = self.get_media_files(item)
            logging.info(f"Potential duplicate: `{name}` with files: {files}")
            file_list = "\n".join(files) if files else "No files found"
            embed.add_field(name=f"{name} [{item.guid}]", value=f"Files:\n{file_list}", inline=False)
        embed.set_footer(text="Please confirm that you want to add this torrent to your library.")
        return embed

    async def selection_callback(self, interaction):
        # Get the torrent_id from the value of the selected option
        try:
            logging.info(interaction.data)
            # Validate that the respondent is the same as the original user
            # if interaction.user.id != interaction.message.embeds[0].footer.text:
            #     return await interaction.response.send_message("You are not this message's author", ephemeral=True)
            torrent_id = interaction.data["values"][0]
            release_entry = self.torrent_cache.get(torrent_id, None)
            if release_entry is None:
                raise ValueError("Release entry not found in cache")
            potential_duplicates = await self.find_potential_duplicates(
                await self.bot.fetch_plex(self.bot.get_guild(interaction.guild_id)),
                release_entry
            )
            if potential_duplicates:
                embed = await self.create_duplicate_warning_embed(release_entry, potential_duplicates)
                view = View()
                confirm = Button(label="Confirm", style=discord.ButtonStyle.green, custom_id=torrent_id)
                view.add_item(confirm)
                cancel = Button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel")
                view.add_item(cancel)
                confirm.callback = self.confirmation_callback
                cancel.callback = self.cancel_callback
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await self.confirmation_callback(interaction)

        except Exception as e:
            logging.exception(e)
            await interaction.response.send_message(f"PlexBot encountered an error adding the torrent to qbittorrent:"
                                                    f"```{e}```")

    async def confirmation_callback(self, interaction):
        try:
            torrent_id = interaction.data["values"][0]
            release_entry = self.torrent_cache.get(torrent_id, None)

            if release_entry is None:
                raise ValueError("Release entry not found in cache")

            library_table = self.bot.database.get_table("qbittorrent_servers")
            library_info = library_table.get_row(guild_id=interaction.guild_id)
            if library_info is None:
                raise ValueError("Library information not found for this guild")

            target_library = None
            if isinstance(release_entry, MovieRelease):
                target_library = library_info["default_movie_library"]
            elif isinstance(release_entry, TVBoxSetRelease):
                target_library = library_info["default_tv_library"]

            embed = discord.Embed(title="Confirm Addition",
                                  description=f"Are you sure you want add the following torrent to the `{target_library}` library?",
                                  color=discord.Color.green())
            # Add the matching info to the embed
            embed.add_field(name=release_entry.original_text, value=self.inline_text(release_entry), inline=False)
            embed.add_field(name="Video Codec", value=f"`{release_entry.video_codec}`", inline=True)
            embed.add_field(name="Audio Codec", value=f"`{release_entry.audio_codec}`", inline=True)
            embed.add_field(name="Source", value=f"`{release_entry.source}`", inline=True)
            embed.add_field(name="Quality", value=f"`{release_entry.quality}`", inline=True)
            embed.add_field(name="Dynamic Range", value=f"`{release_entry.dynamic_range}`", inline=True)
            embed.add_field(name="Audio Layout", value=f"`{release_entry.audio_layout}`", inline=True)
            embed.add_field(name="Group", value=f"`{release_entry.group_name}`", inline=True)
            embed.add_field(name="Streaming Service", value=f"`{release_entry.streaming}`", inline=True)
            embed.add_field(name="Seeders", value=f"`{release_entry.seeders}`", inline=True)
            embed.set_footer(text=f"Please confirm that you want to add this torrent to your library.")
            view = View()
            confirm = Button(label="Confirm", style=discord.ButtonStyle.green, custom_id=f"confirm_{torrent_id}")
            cancel = Button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel")
            view.add_item(confirm)
            view.add_item(cancel)
            confirm.callback = self.add_torrent_callback
            cancel.callback = self.cancel_callback
            await interaction.response.edit_message(embed=embed, view=view)

        except Exception as e:
            logging.exception(e)
            await interaction.response.send_message(f"PlexBot encountered an error adding the torrent to qbittorrent:"
                                                    f"```{e}```")

    async def add_torrent_callback(self, interaction):
        # Get the response message so we can edit it with the result of the torrent addition
        message = await interaction.channel.fetch_message(interaction.message.id)
        try:
            torrent_id = interaction.data["custom_id"].split("confirm_")[1]
            release_entry = self.torrent_cache.get(torrent_id, None)
            plex = await self.bot.fetch_plex(self.bot.get_guild(interaction.guild_id))
            library_table = self.bot.database.get_table("qbittorrent_servers")
            library_info = library_table.get_row(guild_id=interaction.guild_id)
            if release_entry is None:
                raise ValueError("Release entry not found in cache")
            qbittorrent = self.get_qbittorrent(interaction.guild_id)
            if qbittorrent is None:
                raise ConnectionError("qBittorrent not configured for this guild")
            if isinstance(release_entry, MovieRelease):
                target_library = library_info["default_movie_library"]
            elif isinstance(release_entry, TVBoxSetRelease):
                target_library = library_info["default_tv_library"]
            else:
                raise ValueError("Invalid release entry type")

            library = plex.library.section(target_library)

            embed = discord.Embed(title="Adding Torrent...",
                                  description=f"Adding `{release_entry.original_text}` to the `{target_library}` library.",
                                  color=discord.Color.green())
            await message.edit(embed=embed, view=None)
            try:
                result = qbittorrent.torrents_add(urls=release_entry.dl_link, category=release_entry.tracker_abbr,
                                                  savepath=library.locations[0], tags=[f"css_{interaction.message.id}"])
            except qbittorrentapi.APIError as e:
                logging.exception(e)
                embed = discord.Embed(title="Error Adding Torrent",
                                      description=f"An error occurred while adding the torrent to qbittorrent: `{e}`",
                                      color=discord.Color.red())
                await message.edit(embed=embed, view=None)
                return

            if result != "Ok.":
                logging.error(f"Unexpected response from qbittorrent: {result}")
                embed = discord.Embed(title="Error Adding Torrent",
                                      description=f"An unexpected error occurred while adding the torrent to qbittorrent: `{result}`",
                                      color=discord.Color.red())
                await message.edit(embed=embed, view=None)
                return

            embed = discord.Embed(title="Torrent Added",
                                  description=f"`{release_entry.original_text}` has been added to the `{target_library}` library.",
                                  color=discord.Color.green())
            await message.edit(embed=embed, view=None)

            # # Search QBT for the torrent with the tag we just added and then pass this message to the watch_torrent_download method to update the user on the download status
            # torrents = qbittorrent.torrents_info(tags=f"css_{interaction.message.id}")
            # if len(torrents) == 1:
            #     release_entry.torrent_entry = torrents[0]
            #     release_entry.message = message
            #     await self.watch_torrent_download(release_entry)
            # elif len(torrents) == 0:
            #     logging.error(f"Unable to find torrent in qbittorrent with tag css_{interaction.message.id}")
            #     embed = discord.Embed(title="Error Finding Torrent",
            #                           description=f"Unable to find the torrent in qbittorrent after adding it. "
            #                                       f"Progress updates will not be available.",
            #                             color=discord.Color.dark_orange())
            #     await message.edit(embed=embed, view=None)
            # else:
            #     logging.error(f"Multiple torrents found in qbittorrent with tag css_{interaction.message.id}")
            #     embed = discord.Embed(title="Error Finding Torrent",
            #                           description=f"Multiple torrents found in qbittorrent with the tag css_{interaction.message.id}. "
            #                                       f"Progress updates will not be available.",
            #                           color=discord.Color.dark_orange())
            #     for torrent in torrents[:5]:  # List the first 5 torrents found with this tag
            #         embed.add_field(name=f"{torrent.name} [{torrent.hash}]", value=f"Status: {torrent.state}", inline=False)
            #     await message.edit(embed=embed, view=None)

        except Exception as e:
            logging.exception(e)
            embed = discord.Embed(title="Error Adding Torrent",
                                  description=f"An error occurred while adding the torrent to qbittorrent: `{e}`",
                                  color=discord.Color.red())
            await message.edit(embed=embed, view=None)

    async def cancel_callback(self, interaction):
        message = await interaction.channel.fetch_message(interaction.message.id)
        embed = discord.Embed(title="Cancelled",
                              description="The torrent addition has been cancelled.",
                              color=discord.Color.red())
        await message.edit(embed=embed, view=None)

    def inline_text(self, release):
        text_info = [f"WATFAG: {release.watfag:.2f}"]
        if release.size is not None:
            text_info.append(f"`{release.str_size}`")

        if hasattr(release, 'seasons') and release.seasons is not None:
            text_info.append(f"Season `{release.seasons}`")

        if release.seed_status is SeedStatus.LOW:
            text_info.append("⚠️ LOW SEEDS")
        elif release.seed_status is SeedStatus.ZERO:
            text_info.append("❌ NO SEEDS")
        return " | ".join(text_info)

    @hybrid_command(name="css", aliases=["content_self_service"], brief="Search for content to add to Plex",
                    description="Search for content to add to Plex")
    async def content_self_service(self, ctx, *, query):
        try:
            async with ctx.typing():
                results = await self.search_jackett(ctx.guild.id, query)
            if not results:
                await ctx.send(f"No results found for `{query}`")
                return
            results.sort(key=lambda r: r.watfag, reverse=True)
            allow_adding = True
            filtered = results
            # If the user is an administrator, show all results, otherwise filter out results with a WATFAG score below 6.5
            embed = discord.Embed(title=f"Search results for '{query}'", color=discord.Color.blue())
            if not ctx.author.guild_permissions.administrator:
                filtered = list(filter(lambda r: r.watfag >= 6.5, results))

            embed.description = f"Showing {len(filtered[:10])} of {len(results)} results."

            if len(filtered) == 0:
                embed.description = (f"No results with a WATFAG score above 6.5 found. "
                                     f"However `{len(results)}` result{'s' if len(results) != 1 else ''} "
                                     f"were found but are not addable, please contact an administrator to add the following content")
                embed.colour = discord.Color.dark_orange()
                filtered= results
                allow_adding = False
            view = View()
            dropdown = Select(placeholder="Select a release", min_values=1, max_values=1)
            n = 1
            for result in filtered[:10]:  # Limit to top 25 results
                embed.add_field(name=f"{n}. {result.original_text[:99]}",
                                value=self.inline_text(result), inline=False)
                self.torrent_cache[str(hash(result.original_text))] = result
                dropdown.add_option(label=f"{n}. {result.original_text[:99]}",
                                    value=str(hash(result.original_text)))
                n += 1

            embed.set_footer(text=f"{ctx.author.id} | Please select a release to add to plex")
            dropdown.callback = self.selection_callback
            cancel = Button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel")
            cancel.callback = self.cancel_callback
            view.add_item(dropdown)
            view.add_item(cancel)
            if not allow_adding:
                dropdown.disabled = True
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            logging.exception(e)
            await ctx.send(f"PlexBot encountered an error searching for content: `{e}`")

    @command(name="set_qbittorrent", aliases=["set_qb", "set_qbittorrent_url"], brief="Set the qbittorrent URL",
             description="Set the URL for the qbittorrent server", hidden=True)
    @has_permissions(administrator=True)
    async def set_qbittorrent_url(self, ctx, url, username, password):
        try:
            # Set the database entry for the guild
            table = self.bot.database.get_table("qbittorrent_servers")
            table.update_or_add(guild_id=ctx.guild.id, host=url,
                                port=443, username=username, password=password)
            await ctx.send("qbittorrent URL set")
        except Exception as e:
            logging.exception(e)
            await ctx.send(f"PlexBot encountered an error setting the qbittorrent URL: `{e}`")
        finally:
            await ctx.message.delete()

    @command(name="set_jackett", aliases=["set_jk"], brief="Set the Jackett URL",
             description="Set the URL for the Jackett server", hidden=True)
    @has_permissions(administrator=True)
    async def set_jackett_url(self, ctx, url, api_key):
        try:
            # Set the database entry for the guild
            table = self.bot.database.get_table("jackett_servers")
            table.update_or_add(guild_id=ctx.guild.id, host=url, api_key=api_key)
            await ctx.send("Jackett URL set")
            # Update the Jackett instance for this guild
            self.jackett_instances[ctx.guild.id] = Jackett(api_key=api_key, base_url=url)
        except Exception as e:
            logging.exception(e)
            await ctx.send(f"PlexBot encountered an error setting the Jackett URL: `{e}`")
        finally:
            await ctx.message.delete()

    @command(name="set_library", aliases=["set_lib"], brief="Set the Plex library",
             description="Set the Plex library to add content to")
    @has_permissions(administrator=True)
    async def set_library(self, ctx, default_movie, default_tv):
        try:
            # Set the database entry for the guild
            table = self.bot.database.get_table("qbittorrent_servers")
            table.update_or_add(guild_id=ctx.guild.id, default_movie_library=default_movie,
                                default_tv_library=default_tv)
            await ctx.send("Plex library set")
        except Exception as e:
            logging.exception(e)
            await ctx.send(f"PlexBot encountered an error setting the Plex library: `{e}`")

    @command(name="css_info", brief="Get info about the CSS database",
             description="Get info about the CSS database")
    async def css_info(self, ctx):
        try:
            database_size = os.path.getsize("rarbg_db/rarbg_db.sqlite")
            total_entries = self.rargb_database.get("SELECT COUNT(*) FROM items")[0][0]
            default_movie_library = self.bot.database.get_table("qbittorrent_servers").get_row(
                guild_id=ctx.guild.id)["default_movie_library"]
            default_tv_library = self.bot.database.get_table("qbittorrent_servers").get_row(
                guild_id=ctx.guild.id)["default_tv_library"]

            try:
                qbittorrent_status = self.get_qbittorrent(ctx.guild.id).app_version()
                qbittorrent_status = f"Online - {qbittorrent_status}"
                # qbittorrent_free_space = self.get_qbittorrent(ctx.guild.id).app_preferences().free_space_on_disk
                qbittorrent_free_space = 0
            except Exception as e:
                qbittorrent_status = f"Offline - {type(e)}"
                qbittorrent_free_space = 0
                logging.exception(e)

            embed = discord.Embed(title="CSS Database Info", color=discord.Color.blue())
            embed.description = f"Database Size: `{humanize.naturalsize(database_size)}`\n" \
                                f"Total Entries: `{humanize.intcomma(total_entries)}`\n" \
                                f"Default Movie Library: `{default_movie_library}`\n" \
                                f"Default Show Library:  `{default_tv_library}`\n" \
                                f"qBittorrent Status: `{qbittorrent_status}`\n" \
                                f"qBt Free Space: `{humanize.naturalsize(qbittorrent_free_space)}`"
            await ctx.send(embed=embed)
        except Exception as e:
            logging.exception(e)
            await ctx.send(f"PlexBot encountered an error getting the CSS database info: `{e}`")


async def setup(bot):
    try:
        await bot.add_cog(PlexSelfService(bot))
    except Exception as e:
        logging.error(f"Error loading PlexSelfService cog: {e}")
        logging.exception(e)
    else:
        logging.info("PlexSelfService cog loaded successfully")