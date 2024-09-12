from .auction import EnhancedAdvancedAuction

async def setup(bot):
    cog = AdvancedAuction(bot)
    await bot.add_cog(cog)
