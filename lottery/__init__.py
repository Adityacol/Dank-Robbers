from .lottery import Lottery

async def setup(bot):
    await bot.add_cog(Lottery(bot))
    print("Lottery cog setup completed")
