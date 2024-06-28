from .moderation_cog import MessageModeration

async def setup(bot):
    cog = MessageModeration(bot)
    await bot.add_cog(cog)
    await cog.initialize()
