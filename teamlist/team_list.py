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
        self.staff_list_channel_id = None
        self.staff_list_message_id = None
        self.update_interval = 300  # Update interval in seconds (e.g., 300 seconds = 5 minutes)

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
        if role.id not in [r["id"] for r in self.staff_roles]:
            self.staff_roles.append({"name": role.name, "id": role.id})
            self.save_staff_roles()
            await ctx.send(f"Role '{role.name}' added to the staff list.")
            await self.update_staff_list(ctx)
        else:
            await ctx.send(f"Role '{role.name}' is already in the staff list.")

    @commands.command()
    async def remove_role(self, ctx, role: discord.Role):
        role_id = role.id
        for r in self.staff_roles:
            if r["id"] == role_id:
                self.staff_roles.remove(r)
                self.save_staff_roles()
                await ctx.send(f"Role '{role.name}' removed from the staff list.")
                await self.update_staff_list(ctx)
                return
        await ctx.send(f"Role '{role.name}' is not in the staff list.")

    @commands.command()
    async def generate_staff_list(self, ctx):
        channel = ctx.channel
        embed = discord.Embed(title="Our Staff", color=discord.Color.blue())
        for role_info in self.staff_roles:
            role_id = role_info.get("id")
            role_name = role_info.get("name")
            role = ctx.guild.get_role(role_id)
            if role:
                members = role.members
                member_status_list = [
                    f"{member.mention}: {self.get_status_emoji(member.status)} {member.status}"
                    for member in members
                ]
                if member_status_list:
                    embed.add_field(name=role_name, value="\n".join(member_status_list), inline=False)
                else:
                    embed.add_field(name=role_name, value="No members", inline=False)

        # Send or edit the embed
        if self.staff_list_message_id:
            try:
                staff_list_channel = self.bot.get_channel(self.staff_list_channel_id)
                staff_list_message = await staff_list_channel.fetch_message(self.staff_list_message_id)
                await staff_list_message.edit(embed=embed)
            except (discord.NotFound, discord.Forbidden):
                staff_list_message = await channel.send(embed=embed)
                self.staff_list_channel_id = staff_list_message.channel.id
                self.staff_list_message_id = staff_list_message.id
        else:
            staff_list_message = await channel.send(embed=embed)
            self.staff_list_channel_id = staff_list_message.channel.id
            self.staff_list_message_id = staff_list_message.id

    async def auto_update_staff_list(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await self.generate_staff_list()
            await asyncio.sleep(self.update_interval)

    def get_status_emoji(self, status):
        status_emojis = {
            discord.Status.online: ":green_circle:",
            discord.Status.offline: ":black_circle:",
            discord.Status.idle: ":yellow_circle:",
            discord.Status.dnd: ":red_circle:"
        }
        return status_emojis.get(status, ":white_circle:")

def setup(bot):
    cog = StaffListCog(bot)
    bot.add_cog(cog)
    bot.loop.create_task(cog.auto_update_staff_list())
