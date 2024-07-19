from .auction import Auction

async def setup(bot):
    cog = Auction(bot)
    await bot.add_cog(cog)
