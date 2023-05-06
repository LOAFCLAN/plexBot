import asyncio
import copy
import os
import re
import sqlite3

from discord.ext.commands import command, has_permissions, Cog, Context, NotOwner, is_owner
from plexapi.server import PlexServer
import discord.errors as discord_errors
import discord

import ConcurrentDatabase


def table_str_generator(ret):
    # Calculate the longest string in each column
    col_widths = []
    for col in range(len(ret[0])):
        col_widths.append(max([len(str(row[col])) for row in ret]))

    # Generate the table string
    table_str = '```'
    for row in ret:
        table_row = []
        row_len = 0
        for col in range(len(row)):
            # If the row exceeds the max row width truncate it
            if row_len + col_widths[col] + 1 > 85:
                table_row.append("...")
                break
            table_row.append(str(row[col]).center(col_widths[col] + 1))
            row_len += col_widths[col] + 2
        # If the table str exceeds 4000 characters truncate it
        table_str += '|' + '|'.join(table_row) + '|\n'
        if len(table_str) > 1993:
            table_str = table_str[:1993] + '...```'
            return table_str
    return table_str + '```'


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
    @command(name='db_rollback')
    async def db_rollback(self, ctx):
        """Rollback the database to the last backup"""

        # Check if the backup file exists
        if not os.path.isfile('plex_bot.db'):
            await ctx.send('No backup file found')
            return

        # Create a second backup file
        second_backup = sqlite3.connect('plex_bot.db.bak2')
        self.bot.backup_database.backup(target=second_backup)

        # Close all database connections
        self.bot.database.close()
        self.bot.backup_database.close()
        second_backup.close()

        # Copy the backup file to the main database file
        os.remove('plex_bot.db')
        os.rename('plex_bot.db.bak', 'plex_bot.db')

        # Reopen the database connections
        self.bot.database = ConcurrentDatabase.Database('plex_bot.db')
        self.bot.backup_database = sqlite3.connect('plex_bot.db.bak')
        second_backup = sqlite3.connect('plex_bot.db.bak2')
        second_backup.backup(target=self.bot.backup_database)
        second_backup.close()
        await ctx.send('Database rollback complete')

    @is_owner()
    @command(name='sql_eval')
    async def sql_evaluate(self, ctx, *, code):
        """
        Evaluates SQL.
        Await is valid and `{ctx}` is the command context.
        """
        if code.startswith('```'):
            code = code.strip('```').partition('\n')[2].strip()
        else:
            code = code.strip('`').strip()

        table = ''
        e = discord.Embed(type='rich')
        e.add_field(name='Code', value='```sql\n%s\n```' % code, inline=False)
        try:
            cursor = self.bot.database.execute(code)
            ret = cursor.fetchall()
            e.title = 'SQL Evaluation - Success'
            e.color = 0x00FF00
            if len(ret) == 0:
                e.add_field(name='Output', value='```\nNone\n```')
            elif isinstance(ret[0], tuple):
                # Make a table of the results
                table = table_str_generator(ret)
            else:
                e.add_field(name='Output', value='```\n%s\n```' % repr(ret), inline=False)
        except Exception as err:
            e.title = 'SQL Evaluation - Error'
            e.color = 0xFF0000
            e.add_field(name='Error', value='```\n%s\n```' % repr(err))

        msg = await ctx.send('', embed=e)
        if table != '':
            await msg.reply(table)

    @is_owner()
    @command(name='commit_sql')
    async def commit_sql(self, ctx):
        """
        Commits the SQL transaction.
        """
        self.bot.database.commit()
        await ctx.send('SQL transaction committed.')

    @is_owner()
    @command(name='drop_table')
    async def dump_db(self, ctx, *, table):
        """
        Drops a table from the database.
        """
        # Ask for confirmation
        embed = discord.Embed(title='Drop table',
                              description='Are you sure you want to drop the table %s?' % table, color=0xFF0000)
        # Add some details about the table
        cursor = self.bot.database.execute(f"SELECT * FROM {table}")
        if cursor.rowcount == -1:
            embed.add_field(name='Rows', value='Empty')
        else:
            embed.add_field(name='Rows', value='%s' % cursor.rowcount)
        cols = cursor.execute('PRAGMA table_info(%s)' % table).fetchall()
        columns = [col[1] for col in cols]
        embed.add_field(name='Columns', value='%s' % ', '.join(columns))

        msg = await ctx.send('', embed=embed)
        # Add a reaction to the message
        await msg.add_reaction('✅')
        await msg.add_reaction('❌')

        # Wait for a reaction
        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ['✅', '❌']

        try:
            reaction, user = await self.bot.wait_for('reaction_add', check=check, timeout=30)
        except asyncio.TimeoutError:
            return
        # Check if the reaction was a success
        if str(reaction.emoji) == '✅':
            # Drop the table
            del_cursor = self.bot.database.execute(f"DROP TABLE {table}")
            self.bot.database.commit()
            # Send a message
            await ctx.send('Table %s dropped' % table)
        else:
            # Delete the message
            await msg.delete()
            # Send a message
            await ctx.send('Table %s not dropped' % table)

        # Restart the bot
        await self.bot.logout()
        exit(69)

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
        # Get all session watchers and have them dump to the database and their respective channels
        for watcher in self.bot.session_watchers:
            await watcher.bot_shutdown()
        await ctx.send("Restarting...")
        await self.bot.restart()

    @is_owner()
    @command(name="shutdown", help="Shuts down the bot", is_owner=True)
    async def shutdown(self, ctx):
        embed = discord.Embed(title="Shut down?", description="Are you sure you want to shut down?\n"
                                                              "This action is not easily reversed", color=0x00FF00)
        embed.set_footer(text="React with ✅ to confirm or ❌ to cancel")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        def check(reaction, user):
            if user == ctx.author and reaction.message.id == msg.id and reaction.emoji == "✅":
                return True
            elif user == ctx.author and reaction.message.id == msg.id and reaction.emoji == "❌":
                return True
            return False

        try:
            reaction, user = await self.bot.wait_for('reaction_add', check=check, timeout=30)
            if reaction.emoji == "✅":
                await msg.edit(embed=discord.Embed(title="Shut down", description="Shutting down...", color=0x00FF00))
                await msg.clear_reactions()
                await self.bot.shutdown()
            elif reaction.emoji == "❌":
                await msg.edit(embed=discord.Embed(title="Shut down", description="Shutdown cancelled", color=0xFF0000))
        except asyncio.TimeoutError:
            await msg.edit(
                embed=discord.Embed(title="Shut down cancelled", description="Shut down cancelled", color=0xFF0000))
        await msg.clear_reactions()

    @is_owner()
    @command(name="update", is_owner=True)
    async def update(self, ctx):
        """Update the bot from the master branch"""
        msg = await ctx.send("Updating...")
        res = os.popen("git pull").read()
        if res.startswith('Already up to date.') or "CONFLICT (content):" in res:
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
            await self.bot.restart()


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


async def setup(bot):
    await bot.add_cog(maintCog(bot))
