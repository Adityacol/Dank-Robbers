# __init__.py

from .ai_bot import AdvancedAIChatBotCog

def setup(bot):
    bot.add_cog(AdvancedAIChatBotCog(bot))
