from .embedtracker import DailyEmbedTracker

async def setup(bot):
    await bot.add_cog(DailyEmbedTracker(bot))
