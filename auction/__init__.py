from .auction import AdvancedAuctionSystem

async def setup(bot):
    cog = AdvancedAuctionSystem(bot)
    await bot.add_cog(cog)
