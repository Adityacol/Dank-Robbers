from .empiregame import EmpireGame
import discord

async def setup(bot):
    cog = EmpireGame(bot)
    await bot.add_cog(cog)

async def teardown(bot):
    cog = bot.get_cog("EmpireGame")
    if cog:
        bot.tree.remove_command(cog.setup_empire_game.name, type=discord.AppCommandType.chat_input)
        bot.tree.remove_command(cog.save_alias.name, type=discord.AppCommandType.chat_input)
        bot.tree.remove_command(cog.guess_alias.name, type=discord.AppCommandType.chat_input)
        await bot.remove_cog("EmpireGame")
