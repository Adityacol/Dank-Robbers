import discord
from redbot.core import commands
import json
import os
import asyncio

class StaffListCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_file = 'team_config.json'
        self.staff_roles = self.load_staff_roles()
        self.generate_staff_list_task = self.bot.loop.create_task(self.generate_staff_list())

    def load_staff_roles(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as file:
                data = json.load(file)
                return data.get("roles", [])
        return []

    def save_staff_roles(self):
        with open(self.data_file, 'w') as file:
            json.dump({"roles": self.staff_roles}, file, indent=4)

    @commands.command()
    async def add_role(self, ctx, role: discord.Role):
        if role.id not in self.staff_roles:
            self.staff_roles.append(role.id)
            self.save_staff_roles()
            await ctx.send(f"Role '{role.name}' added to the staff list.")
        else:
            await ctx.send(f"Role '{role.name}' is already in the staff list.")

    @commands.command()
    async def remove_role(self, ctx, role: discord.Role):
        if role.id in self.staff_roles:
            self.staff_roles.remove(role.id)
            self.save_staff_roles()
            await ctx.send(f"Role '{role.name}' removed from the staff list.")
        else:
            await ctx.send(f"Role '{role.name}' is not in the staff list.")

    @commands.command()
    async def generate_staff_list(self, ctx):
        embed = discord.Embed(title="Our Staff", color=discord.Color.blue())
        for role_id in self.staff_roles:
            role = ctx.guild.get_role(role_id)
            if role:
                members = role.members
                member_status_list = [
                    f"{member.display_name}: {self.get_status_emoji(member.status)} {member.status}"
                    for member in members
                ]
                if member_status_list:
                    embed.add_field(name=role.name, value="\n".join(member_status_list), inline=False)
                else:
                    embed.add_field(name=role.name, value="No members", inline=False)
        await ctx.send(embed=embed)

    def get_status_emoji(self, status):
        status_emojis = {
            discord.Status.online: ":green_circle:",
            discord.Status.offline: ":black_circle:",
            discord.Status.idle: ":yellow_circle:",
            discord.Status.dnd: ":red_circle:"
        }
        return status_emojis.get(status, ":white_circle:")

    async def generate_staff_list(self):
        while True:
            await self.bot.wait_until_ready()
            channel = self.bot.get_channel(1045701383430606879)  # Replace 'your_channel_id' with the actual channel ID
            if channel:
                embed = discord.Embed(title="Our Staff", color=discord.Color.blue())
                for role_id in self.staff_roles:
                    role = channel.guild.get_role(role_id)
                    if role:
                        members = role.members
                        member_status_list = [
                            f"{member.display_name}: {self.get_status_emoji(member.status)} {member.status}"
                            for member in members
                        ]
                        if member_status_list:
                            embed.add_field(name=role.name, value="\n".join(member_status_list), inline=False)
                        else:
                            embed.add_field(name=role.name, value="No members", inline=False)
                await channel.send(embed=embed)
            await asyncio.sleep(600)  # Update every 10 minutes

def setup(bot):
    bot.add_cog(StaffListCog(bot))
