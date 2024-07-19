from .ai_bot import AdvancedAIChatBotCog

async def setup(bot):
    await bot.add_cog(AdvancedAIChatBotCog(bot))
