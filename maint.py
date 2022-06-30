import os

from discord.ext.commands import command, has_permissions, Cog, Context
from plexapi.server import PlexServer
import discord.errors as discord_errors
import discord


class maintCog(Cog):

    def __init__(self, bot):
        self.bot = bot
        pass

    @command(name="restart", help="Restarts the bot", is_owner=True)
    async def restart(self, ctx):
        await ctx.send("Restarting...")
        await self.bot.shutdown(restart=True)

    @command(name="update", is_owner=True, hidden=True)
    async def update(self, ctx):
        """Update the bot from the master branch"""
        msg = await ctx.send("Updating...")
        res = os.popen("git pull").read()
        if res.startswith('Already up to date.'):
            await ctx.send('```\n' + res + '```')
        else:
            await ctx.send('```\n' + res + '```')
            # Run pip update on requirements.txt
            res = os.popen("pip install -r requirements.txt").read()
            new_res = ""
            for line in res.split('\n'):
                if line.startswith('Requirement already satisfied'):
                    new_res += "Requirement already satisfied...\n"
                else:
                    new_res += line + '\n'
            if len(new_res) > 2000:
                await ctx.send("```\n" + new_res[:2000] + "```")
                await ctx.send("```\n" + new_res[2000:] + "```")
            else:
                await ctx.send('```\n' + new_res + '```')
            await ctx.bot.get_command('restart').callback(ctx)

        await msg.delete()


def setup(bot):
    bot.add_cog(maintCog(bot))
    print("maint.py loaded")
