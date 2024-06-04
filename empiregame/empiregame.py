# File: empiregame.py

import discord
from discord.ext import commands
from redbot.core import commands, app_commands
from redbot.core.bot import Red
import random

class EmpireGame(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.game_active = False
        self.setup_done = False
        self.participants = {}
        self.initial_permissions = {}
        self.turns = {}
        self.failed_turns = {}

    async def join_game(self, interaction: discord.Interaction):
        if len(self.participants) >= 10:
            await interaction.response.send_message("The maximum number of participants (10) has been reached.", ephemeral=True)
            return
        if interaction.user in self.participants:
            await interaction.response.send_message("You have already joined the game.", ephemeral=True)
            return

        # Store initial permissions
        self.initial_permissions[interaction.user] = interaction.user.guild_permissions

        self.participants[interaction.user] = None
        self.failed_turns[interaction.user] = 0
        await interaction.response.send_message(f"{interaction.user.mention} has joined the game.", ephemeral=True)

    @app_commands.command(name="setup_empire_game", description="Setup the Empire game with rules")
    async def setup_empire_game(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Empire Game Rules",
            description=(
                "1. Players save their aliases using `/save_alias`.\n"
                "2. After all aliases are saved, start the game with `/start_empire_game`.\n"
                "3. Players guess the aliases of others using `/guess_alias`.\n"
                "4. Correct guesses grant additional turns.\n"
                "5. Game continues until all aliases are guessed."
            ),
            color=discord.Color.blue()
        )
        join_button = discord.ui.Button(label="Join Game", style=discord.ButtonStyle.primary)
        join_button.callback = self.join_game

        view = discord.ui.View()
        view.add_item(join_button)

        self.setup_done = True
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="start_empire_game", description="Start the Empire game")
    async def start_empire_game(self, interaction: discord.Interaction):
        if not self.setup_done:
            await interaction.response.send_message("Please run `/setup_empire_game` first.", ephemeral=True)
            return
        if not self.participants:
            await interaction.response.send_message("No participants have saved their aliases.", ephemeral=True)
            return

        self.game_active = True
        self.turns = list(self.participants.keys())
        random.shuffle(self.turns)
        await self.start_turn()

    async def start_turn(self):
        if self.turns:
            current_user = self.turns[0]
            await self.bot.get_channel(current_user.dm_channel.id).send(f"{current_user.mention}, it's your turn to guess!")

    async def next_turn(self):
        if self.turns:
            current_user = self.turns.pop(0)
            if self.failed_turns[current_user] == 0:
                self.failed_turns[current_user] += 1
                self.turns.append(current_user)
            else:
                await current_user.edit(mute=True)
                del self.participants[current_user]
                del self.failed_turns[current_user]
            await self.start_turn()

    @app_commands.command(name="save_alias", description="Save your alias for the Empire game")
    @app_commands.describe(alias="Your alias")
    async def save_alias(self, interaction: discord.Interaction, alias: str):
        if not self.setup_done:
            await interaction.response.send_message("Please run `/setup_empire_game` first.", ephemeral=True)
            return
        if interaction.user not in self.participants:
            await interaction.response.send_message("You are not a participant in the game. Join the game first.", ephemeral=True)
            return

        self.participants[interaction.user] = alias
        await interaction.response.send_message(f"Alias '{alias}' saved for {interaction.user.name}.", ephemeral=True)

    @app_commands.command(name="guess_alias", description="Guess the alias of another participant")
    @app_commands.describe(member="The member whose alias you want to guess", alias="Your guess for the alias")
    async def guess_alias(self, interaction: discord.Interaction, member: discord.Member, alias: str):
        if not self.game_active:
            await interaction.response.send_message("The game has not started yet. Run `/start_empire_game`.", ephemeral=True)
            return
        if member not in self.participants:
            await interaction.response.send_message("This member is not a participant in the game.", ephemeral=True)
            return
        if interaction.user != self.turns[0]:
            await interaction.response.send_message("It's not your turn.", ephemeral=True)
            return

        if self.participants[member] == alias:
            await interaction.response.send_message(f"Correct! {member.name}'s alias is {alias}. You get another turn.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Incorrect guess. {member.name}'s alias is not {alias}.", ephemeral=True)
            await self.next_turn()

    @app_commands.command(name="end_empire_game", description="End the Empire game and reset permissions")
    async def end_empire_game(self, interaction: discord.Interaction):
        if not self.game_active:
            await interaction.response.send_message("The game is not active.", ephemeral=True)
            return

        self.game_active = False
        self.setup_done = False

        # Reset permissions for all participants
        for user, permissions in self.initial_permissions.items():
            await user.edit(mute=False)

        self.participants.clear()
        self.initial_permissions.clear()
        self.turns.clear()
        self.failed_turns.clear()

        await interaction.response.send_message("The Empire game has ended. All permissions have been reset.", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} is ready.")

    async def cog_load(self):
        self.bot.tree.add_command(self.setup_empire_game)
        self.bot.tree.add_command(self.start_empire_game)
        self.bot.tree.add_command(self.save_alias)
        self.bot.tree.add_command(self.guess_alias)
        self.bot.tree.add_command(self.end_empire_game)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.setup_empire_game.name)
        self.bot.tree.remove_command(self.start_empire_game.name)
        self.bot.tree.remove_command(self.save_alias.name)
        self.bot.tree.remove_command(self.guess_alias.name)
        self.bot.tree.remove_command(self.end_empire_game.name)

async def setup(bot: Red):
    cog = EmpireGame(bot)
    await bot.add_cog(cog)