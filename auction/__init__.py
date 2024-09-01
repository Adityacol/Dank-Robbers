from .auction import AdvancedAuction

async def setup(bot):
    cog = AdvancedAuction(bot)
    await bot.add_cog(cog)
