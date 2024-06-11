from .roll_track import RollTrack

async def setup(bot):
    cog = RollTrack(bot)
    await bot.add_cog(cog)
