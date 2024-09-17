from .main import AdvancedAuctionSystem

async def setup(bot):
    await bot.add_cog(AdvancedAuctionSystem(bot))