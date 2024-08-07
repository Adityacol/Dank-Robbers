from .team_list import EmbedTracker


async def setup(bot):
    await bot.add_cog(EmbedTracker(bot))