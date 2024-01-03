import asyncio
from typing import List

import plexapi
from discord.ext.commands import command, has_permissions, Cog, BadArgument
import discord.errors as discord_errors
import discord

from loguru import logger as logging


class StatisticsResourceAdvanced:
    # Graph constants
    full_height = '█'
    half_height = '▄'
    empty_height = ' '
    graph_width = 30  # characters wide
    graph_height = 8  # characters tall (excluding labels)

    class Sample:

        def __init__(self, resource):
            self.host_cpu = resource.hostCpuUtilization
            self.plex_cpu = resource.processCpuUtilization
            self.host_mem = resource.hostMemoryUtilization
            self.plex_mem = resource.processMemoryUtilization

            # Create a string representation of the sample for the graph
            self.cpu_graph_string = self.generate_graph_string(self.host_cpu / 100)

        def generate_graph_string(self, value):
            """Generate a string representation of the sample for the graph"""
            # Value is a float between 0 and 1
            # If the value is greater than the graph height, it will just be all full blocks
            # If the value is less than the graph height, it will be a combination of full and half blocks
            # If the value is 0, it will be all empty blocks

            # Height is the number of blocks (full blocks count as 2, half blocks count as 1) that will be filled
            height = int(value * StatisticsResourceAdvanced.graph_height * 2)
            # If the height is greater than the graph height, set it to the graph height
            if height > StatisticsResourceAdvanced.graph_height * 2:
                height = StatisticsResourceAdvanced.graph_height * 2
            graph_string = ""
            # Add the block required for each row
            for row in range(StatisticsResourceAdvanced.graph_height):
                if height > 1:
                    graph_string += StatisticsResourceAdvanced.full_height
                    height -= 2
                elif height == 1:
                    graph_string += StatisticsResourceAdvanced.half_height
                    height -= 1
                else:
                    graph_string += StatisticsResourceAdvanced.empty_height
            # Reverse the string
            graph_string = graph_string[::-1]
            return graph_string

    def __init__(self, resource_list):
        self.resource_list = resource_list
        self.host_cpu_now = self.resource_list[0].hostCpuUtilization
        self.plex_cpu_now = self.resource_list[0].processCpuUtilization
        self.host_mem_now = self.resource_list[0].hostMemoryUtilization
        self.plex_mem_now = self.resource_list[0].processMemoryUtilization
        self.cpu_average, self.mem_average = self.calculate_average()
        self.cpu_graph = self.generate_cpu_graph()
        self.mem_graph = self.generate_mem_graph()

    def calculate_average(self):
        """Calculate the average utilization of all resources"""
        cpu_total = 0
        mem_total = 0
        for resource in self.resource_list:
            cpu_total += resource.hostCpuUtilization
            mem_total += resource.hostMemoryUtilization
        cpu_average = cpu_total / len(self.resource_list)
        mem_average = mem_total / len(self.resource_list)
        return cpu_average, mem_average

    def generate_cpu_graph(self):
        """Generate a simple ascii graph for cpu utilization"""
        cpu_graph = "```"
        # Sort the list by time
        # self.resource_list.sort(key=lambda x: x.at)
        sample_slice = self.resource_list[:self.graph_width]  # get the samples that fit in the graph
        sample_slice = [self.Sample(sample) for sample in sample_slice]
        sample_slice.reverse()  # reverse the list so the oldest sample is first
        # To create the graph we need to go one row at a time and add the appropriate character for each column
        # To do this each sample needs to be
        for row in range(self.graph_height):
            for sample in sample_slice:
                cpu_graph += sample.cpu_graph_string[row]
            cpu_graph += "\n"
        cpu_graph += "```"
        return cpu_graph

    def generate_mem_graph(self):
        """Generate a simple ascii graph for memory utilization"""
        return "TODO"


class PlexUtilization(Cog):

    def __init__(self, bot):
        self.bot = bot

    async def generate_embed(self, ctx):
        plex_utilization = ctx.plex.resources()
        plex_utilization.sort(key=lambda x: x.at, reverse=True)
        # the plex resource object is a list of StatisticsResources objects
        # We want to create an embed with the following information in this layout: (each line is a field name/value pair)
        #   SYS CPU | PLEX CPU | AVERAGE CPU
        #   04.32%  |  02.34%  |   03.33%
        #   CPU USAGE GRAPH (ASCII, 4 lines tall, 20 characters wide)
        #   SYS MEM | PLEX MEM | AVERAGE MEM
        #   04.32%  |  02.34%  |   03.33%
        #   MEM USAGE GRAPH (ASCII, 4 lines tall, 20 characters wide)

        stats = StatisticsResourceAdvanced(plex_utilization)

        # Create the embed
        embed = discord.Embed(title="Plex Server Utilization", color=discord.Color.blue())
        # Create the fields
        embed.add_field(name="HOST CPU", value=f"{stats.host_cpu_now:.2f}%")
        embed.add_field(name="PLEX CPU", value=f"{stats.plex_cpu_now:.2f}%")
        embed.add_field(name="AVERAGE CPU", value=f"{stats.cpu_average:.2f}%")
        embed.add_field(name="CPU USAGE GRAPH", value=stats.cpu_graph, inline=False)
        embed.add_field(name="HOST MEM", value=f"{stats.host_mem_now:.2f}%")
        embed.add_field(name="PLEX MEM", value=f"{stats.plex_mem_now:.2f}%")
        embed.add_field(name="AVERAGE MEM", value=f"{stats.mem_average:.2f}%")
        embed.add_field(name="MEM USAGE GRAPH", value=stats.mem_graph, inline=False)
        embed.set_footer(text="Last resource update: " + plex_utilization[0].at.strftime("%Y-%m-%d %H:%M:%S"))
        return embed

    @command(name="utilization", aliases=["util"], brief="Get Plex server utilization", help="Get Plex server utilization")
    @has_permissions(administrator=True)
    async def utilization(self, ctx):
        """Get Plex server utilization"""
        msg = await ctx.send("Loading...")
        while True:
            try:
                embed = await self.generate_embed(ctx)
                await msg.edit(content=None, embed=embed)
                await asyncio.sleep(5)
            except discord_errors.HTTPException as e:
                if e.code == 50035:
                    # The embed is too large
                    await msg.edit(content="The embed is too large to send. Please try again later.")
                    break
                else:
                    raise e
            except Exception as e:
                raise e



async def setup(bot):
    await bot.add_cog(PlexUtilization(bot))
    logging.info("PlexUtilization loaded successfully")
