from .lottery import Lottery

async def setup(bot):
    await bot.add_cog(Lottery(bot))
