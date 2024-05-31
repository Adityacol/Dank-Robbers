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
        self.webhook_url = "https://discord.com/api/webhooks/1246058850176467005/GLijVlr3Em4WG_TRNEjHHaaS0fKFgaukAJW0s4oigoaRS7SHBnu39R0yQreU6vWFqqST"  # Replace with your webhook URL
        self.message_id = None  # Variable to store the message ID of the sent embed
        self.generate_staff_list_task = self.bot.loop.create_task(self.auto_update_staff_list())

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
            await self.update_staff_list()
        else:
            await ctx.send(f"Role '{role.name}' is already in the staff list.")

    @commands.command()
    async def remove_role(self, ctx, role: discord.Role):
        if role.id in self.staff_roles:
            self.staff_roles.remove(role.id)
            self.save_staff_roles()
            await ctx.send(f"Role '{role.name}' removed from the staff list.")
            await self.update_staff_list()
        else:
            await ctx.send(f"Role '{role.name}' is not in the staff list.")

    async def generate_staff_list_embed(self):
        channel = self.bot.get_channel(self.channel_id)
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
        return embed

    async def auto_update_staff_list(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(self.channel_id)
        if channel:
            while not self.bot.is_closed():
                embed = await self.generate_staff_list_embed()
                if self.message_id:
                    webhook = discord.Webhook.from_url(self.webhook_url, adapter=discord.RequestsWebhookAdapter())
                    await webhook.edit_message(message_id=self.message_id, embed=embed)
                else:
                    webhook = discord.Webhook.from_url(self.webhook_url, adapter=discord.RequestsWebhookAdapter())
                    message = await webhook.send(embed=embed)
                    self.message_id = message.id
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
