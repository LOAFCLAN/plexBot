import copy
import os
import re

from discord.ext.commands import command, has_permissions, Cog, Context, NotOwner, is_owner
from plexapi.server import PlexServer
import discord.errors as discord_errors
import discord


class maintCog(Cog):

    def __init__(self, bot):
        self.bot = bot
        pass

    eval_globals = {}
    for module in ('asyncio', 'collections', 'discord', 'inspect', 'itertools'):
        eval_globals[module] = __import__(module)
    eval_globals['__builtins__'] = __import__('builtins')

    @is_owner()
    @command(name='eval')
    async def evaluate(self, ctx, *, code):
        """
        Evaluates Python.
        Await is valid and `{ctx}` is the command context.
        """
        if code.startswith('```'):
            code = code.strip('```').partition('\n')[2].strip()  # Remove multiline code blocks
        else:
            code = code.strip('`').strip()  # Remove single-line code blocks, if necessary

        e = discord.Embed(type='rich')
        e.add_field(name='Code', value='```py\n%s\n```' % code, inline=False)
        try:
            locals_ = locals()
            load_function(code, self.eval_globals, locals_)
            ret = await locals_['evaluated_function'](ctx)

            e.title = 'Python Evaluation - Success'
            e.color = 0x00FF00
            e.add_field(name='Output', value='```\n%s (%s)\n```' % (repr(ret), type(ret).__name__), inline=False)
        except Exception as err:
            e.title = 'Python Evaluation - Error'
            e.color = 0xFF0000
            e.add_field(name='Error', value='```\n%s\n```' % repr(err))
        await ctx.send('', embed=e)

    @is_owner()
    @command(name='su', pass_context=True)
    async def pseudo(self, ctx, user: discord.Member, *, command):
        """Aka Switch User"""
        msg = copy.copy(ctx.message)
        msg.author = user
        msg.content = command
        context = await self.bot.get_context(msg)
        context.is_pseudo = True  # adds new flag to bypass ratelimit
        # let's also add a log of who ran pseudo
        await self.bot.invoke(context)

    @is_owner()
    @command(name="restart", help="Restarts the bot", is_owner=True)
    async def restart(self, ctx):
        await ctx.send("Restarting...")
        await self.bot.shutdown(restart=True)

    @is_owner()
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
            await msg.edit(content="Restarting...")
            await self.bot.shutdown(restart=True)


def load_function(code, globals_, locals_):
    """Loads the user-evaluted code as a function so it can be executed."""
    function_header = 'async def evaluated_function(ctx):'

    lines = code.splitlines()
    if len(lines) > 1:
        indent = 4
        for line in lines:
            line_indent = re.search(r'\S', line).start()  # First non-WS character is length of indent
            if line_indent:
                indent = line_indent
                break
        line_sep = '\n' + ' ' * indent
        exec(function_header + line_sep + line_sep.join(lines), globals_, locals_)
    else:
        try:
            exec(function_header + '\n\treturn ' + lines[0], globals_, locals_)
        except SyntaxError as err:  # Either adding the 'return' caused an error, or it's user error
            if err.text[err.offset - 1] == '=' or err.text[err.offset - 3:err.offset] == 'del' \
                    or err.text[err.offset - 6:err.offset] == 'return':  # return-caused error
                exec(function_header + '\n\t' + lines[0], globals_, locals_)
            else:  # user error
                raise err


def setup(bot):
    bot.add_cog(maintCog(bot))
    print("maint.py loaded")
