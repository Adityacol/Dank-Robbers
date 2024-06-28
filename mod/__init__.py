from .moderation_cog import MessageModeration

async def setup(bot):
    cog = MessageModeration(bot)
    bot.add_cog(cog)
    await cog.initialize()
