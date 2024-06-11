from .embed_tracker import EmbedTracker

async def setup(bot):
    cog = EmbedTracker(bot)
    await bot.add_cog(cog)
