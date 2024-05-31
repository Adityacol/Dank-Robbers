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
        self.update_interval = 60  # Update interval in seconds (e.g., 600 seconds = 10 minutes)
        self.channel_id = 1045701383430606879  # Replace CHANNEL_ID with the actual channel ID
        self.staff_list_message_id = None  # Store the staff list message ID
        self.generate_staff_list_task = self.bot.loop.create_task(self.auto_update_staff_list())

    def load_staff_roles(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as file:
                data = json.load(file)
                return data.get("roles", [])
        return []

    async def generate_staff_list(self):
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            embed = discord.Embed(title="Our Staff", color=discord.Color.blue())
            for role_info in self.staff_roles:
                role_id = role_info.get("id")
                role_name = role_info.get("name")
                role = discord.utils.get(channel.guild.roles, id=role_id)
                if role:
                    members = role.members
                    member_status_list = [
                        f"{self.get_status_emoji(member.status)} {member.display_name}"
                        for member in members
                    ]
                    if member_status_list:
                        embed.add_field(name=role_name, value="\n".join(member_status_list), inline=False)
                    else:
                        embed.add_field(name=role_name, value="No members", inline=False)
            if self.staff_list_message_id:
                staff_list_message = await channel.fetch_message(self.staff_list_message_id)
                await staff_list_message.edit(embed=embed)
            else:
                staff_list_message = await channel.send(embed=embed)
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
    bot.add_cog(StaffListCog(bot))
