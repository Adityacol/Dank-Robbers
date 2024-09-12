from .auction import EnhancedAdvancedAuction

async def setup(bot):
    cog = EnhancedAdvancedAuction(bot)
    await bot.add_cog(cog)
