from .auction import SuperEnhancedAdvancedAuction

async def setup(bot):
    cog = SuperEnhancedAdvancedAuction(bot)
    await bot.add_cog(cog)
