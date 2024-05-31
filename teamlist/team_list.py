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
        self.update_interval = 600  # Update interval in seconds (e.g., 600 seconds = 10 minutes)
        self.channel_id = 1045701383430606879  # Replace CHANNEL_ID with the actual channel ID
        self.staff_list_message = None  # Store the staff list message
        self.generate_staff_list_task = self.bot.loop.create_task(self.auto_update_staff_list())

    def load_staff_roles(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as file:
                data = json.load(file)
                return data.get("roles", [])
        return []

    async def generate_staff_list_embed(self):
        channel = self.bot.get_channel(self.channel_id)
        embed = discord.Embed(title="Our Staff", color=discord.Color.blue())
        embed.set_thumbnail(url="https://images-ext-1.discordapp.net/external/ddLLWUutTzUJUABcpTJl_xFqke-4fNkje4IYxEiHiZM/%3Fformat%3Dwebp%26width%3D211%26height%3D196/https/images-ext-1.discordapp.net/external/R6VId5otSXLbdUlt4WtsUpmUlvBPeWCdGuafoWbsF_A/%253Fformat%253Dwebp%2526width%253D172%2526height%253D160/https/images-ext-1.discordapp.net/external/cm4p0Ewcngpp2El0CF6XTgaTwbXj0FKBEbxnox3uRlw/%25253Fsize%25253D240%252526quality%25253Dlossless/https/cdn.discordapp.com/emojis/1038110732383961209.webp?format=webp&width=160&height=148")  # Replace ICON_URL with the URL of the icon
        for role_id in self.staff_roles:
            role = channel.guild.get_role(role_id)
            if role:
                members = role.members
                member_status_list = [
                    f"{self.get_status_emoji(member.status)} {member.display_name}"
                    for member in members
                ]
                if member_status_list:
                    embed.add_field(name=role.name, value="\n".join(member_status_list), inline=False)
                else:
                    embed.add_field(name=role.name, value="No members", inline=False)
        embed.set_image(url="https://images-ext-1.discordapp.net/external/Jix1PZm5CLa9S1B6_nnTwtDgZRR_P1ACE9-h2NeGtlA/%3Fformat%3Dwebp%26quality%3Dlossless%26width%3D1148%26height%3D280/https/images-ext-1.discordapp.net/external/fveQb3JWpUuhkPQ6lgZiNqQcssFLceGKIjNiL6xrd_0/%253Fformat%253Dwebp%2526quality%253Dlossless%2526width%253D1044%2526height%253D255/https/images-ext-2.discordapp.net/external/7oZZFuziueGVvFwbvwmUJnS1KnRASQGy00B7fK2UtU0/https/www.helloexmouth.co.uk/wp-content/uploads/spheader-meettheteam.png?format=webp&quality=lossless&width=870&height=212")  # Replace IMAGE_URL with the URL of the image
        return embed

    async def auto_update_staff_list(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            while not self.bot.is_closed():
                embed = await self.generate_staff_list_embed()
                if not self.staff_list_message:
                    self.staff_list_message = await channel.send(embed=embed)
                else:
                    await self.staff_list_message.edit(embed=embed)
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
