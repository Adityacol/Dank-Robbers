import discord
from redbot.core import commands
from redbot.core.data_manager import cog_data_path
import json
import os
import asyncio

class StaffListCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data_file = cog_data_path(self) / 'team_config.json'
        self.staff_roles = self.load_staff_roles()
        self.staff_list_channel_id = None
        self.staff_list_message_id = None
        self.update_interval = 600  # Update interval in seconds (e.g., 600 seconds = 10 minutes)
        self.bot.loop.create_task(self.auto_update_staff_list())

    def load_staff_roles(self):
        if self.data_file.exists():
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
            await self.update_staff_list(ctx.guild)
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
                await self.update_staff_list(ctx.guild)
                return
        await ctx.send(f"Role '{role.name}' is not in the staff list.")

    @commands.command()
    async def generate_staff_list(self, ctx):
        channel = ctx.channel
        embed = await self.create_staff_list_embed(ctx.guild)
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

    async def create_staff_list_embed(self, guild):
        embed = discord.Embed(title="Our Staff", color=discord.Color.blue())
        embed.set_thumbnail(url="https://images-ext-1.discordapp.net/external/Jix1PZm5CLa9S1B6_nnTwtDgZRR_P1ACE9-h2NeGtlA/%3Fformat%3Dwebp%26quality%3Dlossless%26width%3D1148%26height%3D280/https/images-ext-1.discordapp.net/external/fveQb3JWpUuhkPQ6lgZiNqQcssFLceGKIjNiL6xrd_0/%253Fformat%253Dwebp%2526quality%253Dlossless%2526width%253D1044%2526height%253D255/https/images-ext-2.discordapp.net/external/7oZZFuziueGVvFwbvwmUJnS1KnRASQGy00B7fK2UtU0/https/www.helloexmouth.co.uk/wp-content/uploads/spheader-meettheteam.png?format=webp&quality=lossless&width=870&height=212")  # Replace with your thumbnail URL
        embed.set_footer(text="Footer text here", icon_url="https://path.to/your/footer_icon.jpg")  # Replace with your footer icon URL
        embed.set_image(url="https://images-ext-1.discordapp.net/external/ddLLWUutTzUJUABcpTJl_xFqke-4fNkje4IYxEiHiZM/%3Fformat%3Dwebp%26width%3D211%26height%3D196/https/images-ext-1.discordapp.net/external/R6VId5otSXLbdUlt4WtsUpmUlvBPeWCdGuafoWbsF_A/%253Fformat%253Dwebp%2526width%253D172%2526height%253D160/https/images-ext-1.discordapp.net/external/cm4p0Ewcngpp2El0CF6XTgaTwbXj0FKBEbxnox3uRlw/%25253Fsize%25253D240%252526quality%25253Dlossless/https/cdn.discordapp.com/emojis/1038110732383961209.webp?format=webp&width=160&height=148")  # Replace with your footer image URL

        for role_info in self.staff_roles:
            role_id = role_info.get("id")
            role_name = role_info.get("name")
            role = guild.get_role(role_id)
            if role:
                members = role.members
                member_status_list = [
                    f"<a:arrow:1075563743477497946> {member.mention} {self.get_status_emoji(member.status)}"
                    for member in members
                ]
                if member_status_list:
                    field_value = "\n".join(member_status_list)
                    if len(field_value) > 1024:
                        field_value = field_value[:1021] + "..."  # Truncate if necessary
                    embed.add_field(name=role_name, value=field_value, inline=False)
                else:
                    embed.add_field(name=role_name, value="No members", inline=False)
        return embed

    async def auto_update_staff_list(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            if self.staff_list_channel_id and self.staff_list_message_id:
                staff_list_channel = self.bot.get_channel(self.staff_list_channel_id)
                if staff_list_channel:
                    try:
                        staff_list_message = await staff_list_channel.fetch_message(self.staff_list_message_id)
                        embed = await self.create_staff_list_embed(staff_list_channel.guild)
                        await staff_list_message.edit(embed=embed)
                    except (discord.NotFound, discord.Forbidden):
                        self.staff_list_message_id = None
                        self.staff_list_channel_id = None
            await asyncio.sleep(self.update_interval)

    async def update_staff_list(self, guild):
        if self.staff_list_channel_id and self.staff_list_message_id:
            staff_list_channel = self.bot.get_channel(self.staff_list_channel_id)
            if staff_list_channel:
                try:
                    staff_list_message = await staff_list_channel.fetch_message(self.staff_list_message_id)
                    embed = await self.create_staff_list_embed(guild)
                    await staff_list_message.edit(embed=embed)
                except (discord.NotFound, discord.Forbidden):
                    self.staff_list_message_id = None
                    self.staff_list_channel_id = None

    def get_status_emoji(self, status):
        status_emojis = {
            discord.Status.online: "<:onlinestatus:1246105208040329337>",  # Replace with your custom emoji ID
            discord.Status.offline: "<:offlinestatus:1246105188755046400>",  # Replace with your custom emoji ID
            discord.Status.idle: "<:idlestatus:1246105216848232560>",  # Replace with your custom emoji ID
            discord.Status.dnd: "<:dndstatus:1246105225144569977>"  # Replace with your custom emoji ID
        }
        return status_emojis.get(status, ":white_circle:")

def setup(bot):
    bot.add_cog(StaffListCog(bot))
