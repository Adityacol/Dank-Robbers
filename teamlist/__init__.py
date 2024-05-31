from .team_list import StaffListCog

async def setup(bot):
    await bot.add_cog(StaffListCog(bot))
