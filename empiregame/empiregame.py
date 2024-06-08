import discord
import asyncio
import random
from redbot.core import commands, app_commands
from redbot.core.bot import Red
from typing import Dict, List

ROLE_ID = 899916792447766528
GAME_ROLE_ID = 1030538893088534549  # Role to be added/removed
ALIAS_WORD_LIMIT = 3  # Set the word limit for aliases
MAX_PLAYERS = 10  # Decreased player limit to 10

def has_role(interaction: discord.Interaction):
    return any(role.id == ROLE_ID for role in interaction.user.roles)

class Player:
    def __init__(self, user_object: discord.Member):
        self.user_object = user_object
        self.alias = None
        self.alive = True

class EmpireGame(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.game_setup = False
        self.game_started = False
        self.players = {}
        self.turn_order = []
        self.current_turn = 0
        self.joining_channel = None
        self.host = None
        self.turn_timer = None
        self.join_task = None
        self.missed_turns = {}
        self.correct_guess = False  # Flag to track correct guess
        self.view = None

    @app_commands.command(name="setup_empire_game")
    @app_commands.check(has_role)
    async def setup_empire_game(self, interaction: discord.Interaction):
        """Sets up the Empire game with the rules and a join button."""
        if self.game_setup or self.game_started:
            await interaction.response.send_message("❗ A game is already in progress or setup.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="Empire Game Setup",
            description=(
                "Rules\n"
                "・You can only save your alias once. No keyboard smashes allowed or making it break the rules.\n"
                "・If you miss two turns you’ll be disqualified.\n"
                f"・Max is {MAX_PLAYERS} players.\n\n"
            ),
            color=discord.Color.purple()
        )
        embed.set_footer(text="Empire Game | Join now!")
        embed.set_image(url="https://media.discordapp.net/attachments/1124416523910516736/1247270073987629067/image.png?ex=665f6a46&is=665e18c6&hm=3f7646ef6790d96e8c5b6f93bf45e1c57179fd809ef4d034ed1d330287d5ce7b&=&format=webp&quality=lossless&width=836&height=557")

        join_button = discord.ui.Button(label="Join", style=discord.ButtonStyle.success)
        join_button.callback = self.join_button_callback

        leave_button = discord.ui.Button(label="Leave", style=discord.ButtonStyle.danger)
        leave_button.callback = self.leave_button_callback

        start_button = discord.ui.Button(label="Start Game", style=discord.ButtonStyle.primary)
        start_button.callback = self.start_button_callback

        cancel_button = discord.ui.Button(label="Cancel Game", style=discord.ButtonStyle.danger)
        cancel_button.callback = self.cancel_button_callback

        explain_button = discord.ui.Button(label="Explain", style=discord.ButtonStyle.secondary)
        explain_button.callback = self.explain_button_callback

        self.view = discord.ui.View()
        self.view.add_item(join_button)
        self.view.add_item(leave_button)
        self.view.add_item(start_button)
        self.view.add_item(cancel_button)
        self.view.add_item(explain_button)

        await interaction.response.send_message(embed=embed, view=self.view)
        self.joining_channel = interaction.channel
        self.players = {}
        self.turn_order = []
        self.current_turn = 0
        self.game_setup = True
        self.game_started = False
        self.host = interaction.user.id
        self.missed_turns = {}

    async def join_button_callback(self, interaction: discord.Interaction):
        if not self.game_setup:
            await interaction.response.send_message("❗ The game is not currently being set up.", ephemeral=True)
            return
        if len(self.players) >= MAX_PLAYERS:
            await interaction.response.send_message(f"❗ The game already has the maximum number of players ({MAX_PLAYERS}).", ephemeral=True)
            return
        if interaction.user.id in self.players:
            await interaction.response.send_message("❗ You have already joined the game.", ephemeral=True)
            return
        player = Player(interaction.user)
        self.players[interaction.user.id] = player
        self.missed_turns[interaction.user.id] = 0
        await self.update_join_embed(interaction)

    async def leave_button_callback(self, interaction: discord.Interaction):
        if not self.game_setup:
            await interaction.response.send_message("❗ The game is not currently being set up.", ephemeral=True)
            return
        if interaction.user.id not in self.players:
            await interaction.response.send_message("❗ You are not part of the game.", ephemeral=True)
            return
        self.players.pop(interaction.user.id)
        self.missed_turns.pop(interaction.user.id)
        await self.update_join_embed(interaction)

    async def update_join_embed(self, interaction: discord.Interaction):
        players_list = "\n\n".join([interaction.guild.get_member(pid).mention for pid in self.players])
        embed = discord.Embed(
            title="Empire Game Setup",
            description=(
                "Rules\n"
                "・You can only save your alias once. No keyboard smashes allowed or making it break the rules.\n"
                "・If you miss two turns you’ll be disqualified.\n"
                f"・Max is {MAX_PLAYERS} players.\n\n"
                f"Players Joined ({len(self.players)}/{MAX_PLAYERS}):\n{players_list}"
            ),
            color=discord.Color.purple()
        )
        embed.set_footer(text="Empire Game | Join now!")
        embed.set_image(url="https://media.discordapp.net/attachments/1124416523910516736/1247270073987629067/image.png?ex=665f6a46&is=665e18c6&hm=3f7646ef6790d96e8c5b6f93bf45e1c57179fd809ef4d034ed1d330287d5ce7b&=&format=webp&quality=lossless&width=836&height=557")

        await interaction.response.edit_message(embed=embed)

    async def start_button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.host:
            await interaction.response.send_message("❗ Only the host can start the game.", ephemeral=True)
            return
        if len(self.players) < 2:
            await interaction.response.send_message("❗ Not enough players joined the game.", ephemeral=True)
            return
        self.game_setup = False
        self.view.children[2].disabled = True  # Disable the start button
        await interaction.edit_original_response(view=self.view)
        await self.start_game(interaction)

    async def cancel_button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.host:
            await interaction.response.send_message("❗ Only the host can cancel the game.", ephemeral=True)
            return
        await interaction.response.send_message("❗ The game has been cancelled.")
        await self.reset_game()

    async def explain_button_callback(self, interaction: discord.Interaction):
        rules = (
            "How to Play:\n"
            "・You’ll have 60s to save your alias with /save alias\n"
            "・When it’s your turn, guess an alias using /guess alias\n"
            "・If correct, you get another guess.\n"
            "・If incorrect, the next player will get a turn.\n"
            "・Last one remaining wins.\n"
        )
        await interaction.response.send_message(rules, ephemeral=True)

    async def start_game(self, interaction: discord.Interaction):
        self.turn_order = list(self.players.keys())
        random.shuffle(self.turn_order)
        self.game_started = True
        for player in self.players.values():
            role = interaction.guild.get_role(GAME_ROLE_ID)
            await player.user_object.add_roles(role)
        await self.notify_players_to_save_alias(interaction)

    async def notify_players_to_save_alias(self, interaction: discord.Interaction):
        players_mentions = " ".join([interaction.guild.get_member(pid).mention for pid in self.players])
        embed = discord.Embed(
            title="Game Started!",
            description="Players have 60 seconds to save their aliases using `/save_alias`.",
            color=discord.Color.green()
        )
        await interaction.channel.send(content=players_mentions, embed=embed)
        await asyncio.sleep(60)
        await self.check_aliases(interaction)

    async def check_aliases(self, interaction: discord.Interaction):
        eliminated_players = []
        for player in self.players.values():
            if player.alias is None:
                role = interaction.guild.get_role(GAME_ROLE_ID)
                await player.user_object.remove_roles(role)
                player.alive = False
                eliminated_players.append(player.user_object.mention)
        
        if eliminated_players:
            eliminated_message = "The following players are eliminated for not saving an alias in time:\n" + "\n".join(eliminated_players)
            await interaction.channel.send(eliminated_message)
        
        if sum(1 for player in self.players.values() if player.alive) < 2:
            await self.announce_winner(interaction)
            return

        await self.start_guessing(interaction)

    @app_commands.command(name="save_alias")
    async def save_alias(self, interaction: discord.Interaction, alias: str):
        """Saves the player's alias."""
        if not self.game_started:
            await interaction.response.send_message("❗ The game has not started yet.", ephemeral=True)
            return
        if interaction.user.id not in self.players:
            await interaction.response.send_message("❗ You are not a part of the game.", ephemeral=True)
            return
        player = self.players[interaction.user.id]
        if player.alias is not None:
            await interaction.response.send_message("❗ You have already saved your alias.", ephemeral=True)
            return
        if len(alias.split()) > ALIAS_WORD_LIMIT:
            await interaction.response.send_message(f"❗ Your alias must be {ALIAS_WORD_LIMIT} words or less.", ephemeral=True)
            return
        if alias in [p.alias for p in self.players.values()]:
            await interaction.response.send_message("❗ This alias has already been taken. Please choose another one.", ephemeral=True)
            return
        player.alias = alias
        await interaction.response.send_message("✅ Your alias has been saved.", ephemeral=True)
        if all(p.alias is not None for p in self.players.values()):
            await self.start_guessing(interaction)

    async def start_guessing(self, interaction: discord.Interaction):
        if not self.game_started:
            return

        if sum(1 for player in self.players.values() if player.alive) < 2:
            await self.announce_winner(interaction)
            return

        await self.continue_turn(interaction)

    async def continue_turn(self, interaction: discord.Interaction):
        while True:
            player = self.players[self.turn_order[self.current_turn]]
            if not player.alive:
                self.advance_turn()
                continue

            shuffled_aliases = random.sample([p.alias for p in self.players.values() if p.alive], sum(1 for p in self.players.values() if p.alive))
            players_aliases = list(zip([p.user_object.mention for p in self.players.values() if p.alive], shuffled_aliases))
            players_field = "\n".join([player for player, _ in players_aliases])
            aliases_field = "\n".join([alias for _, alias in players_aliases])

            embed = discord.Embed(
                title=f"{player.user_object.display_name}'s turn!",
                color=discord.Color.green()
            )
            embed.add_field(name="Players", value=players_field, inline=True)
            embed.add_field(name="Aliases", value=aliases_field, inline=True)
            await interaction.channel.send(content=player.user_object.mention, embed=embed)

            if self.turn_timer:
                self.turn_timer.cancel()
            self.turn_timer = self.bot.loop.create_task(self.turn_timeout(interaction))

            break

    async def turn_timeout(self, interaction: discord.Interaction):
        await asyncio.sleep(60)
        if not self.game_started:
            return

        player = self.players[self.turn_order[self.current_turn]]
        self.missed_turns[player.user_object.id] += 1

        if self.missed_turns[player.user_object.id] >= 2:
            await interaction.channel.send(f"❗ {player.user_object.mention} didn't guess an alias for 2 rounds and was eliminated.")
            role = interaction.guild.get_role(GAME_ROLE_ID)
            await player.user_object.remove_roles(role)
            player.alive = False

            if sum(1 for p in self.players.values() if p.alive) < 2:
                await self.announce_winner(interaction)
                return

        await interaction.channel.send(f"❗ {player.user_object.mention} took too long to guess. Moving to the next player.")
        self.advance_turn()
        await self.start_guessing(interaction)

    @app_commands.command(name="guess_alias")
    async def guess_alias(self, interaction: discord.Interaction, member: discord.Member, guessed_alias: str):
        """Allows a player to guess an alias."""
        if not self.game_started:
            await interaction.response.send_message("❗ The game has not started yet.", ephemeral=True)
            return
        player = self.players[self.turn_order[self.current_turn]]
        if interaction.user.id != player.user_object.id:
            await interaction.response.send_message("❗ It's not your turn.", ephemeral=True)
            return
        if guessed_alias not in [p.alias for p in self.players.values()]:
            await interaction.response.send_message("❗ This alias is not valid.", ephemeral=True)
            return
        if member.id == interaction.user.id:
            await interaction.response.send_message("❗ You cannot guess your own alias.", ephemeral=True)
            return

        self.missed_turns[interaction.user.id] = 0  # Reset missed turns on successful guess

        target_player = self.players[member.id]
        if target_player.alias == guessed_alias:
            await interaction.response.send_message(f"🎉 Correct guess! {member.mention} was eliminated.")
            role = interaction.guild.get_role(GAME_ROLE_ID)
            await target_player.user_object.remove_roles(role)
            target_player.alive = False
            self.correct_guess = True
            if sum(1 for p in self.players.values() if p.alive) < 2:
                await self.announce_winner(interaction)
                return
            await self.continue_turn(interaction)  # Grant an extra turn
        else:
            await interaction.response.send_message(f"❌ Wrong guess. It's now the next player's turn.")
            self.correct_guess = False
            self.advance_turn()
            await self.start_guessing(interaction)

    async def announce_winner(self, interaction: discord.Interaction):
        alive_players = [p for p in self.players.values() if p.alive]
        if not alive_players:
            await interaction.channel.send("❗ There are no players left in the game.")
            await self.reset_game()
            return
        winner = alive_players[0]
        role = interaction.guild.get_role(GAME_ROLE_ID)
        await winner.user_object.remove_roles(role)
        embed = discord.Embed(
            title="🏆 We Have a Winner!",
            description=f"Congratulations to {winner.user_object.mention} for winning the Empire Game!",
            color=discord.Color.gold()
        )
        await interaction.channel.send(embed=embed)
        await self.reset_game()

    def advance_turn(self):
        if not self.correct_guess:
            self.current_turn = (self.current_turn + 1) % len(self.turn_order)
        self.correct_guess = False

    async def reset_game(self):
        self.game_setup = False
        self.game_started = False
        self.players = {}
        self.turn_order = []
        self.current_turn = 0
        self.joining_channel = None
        self.host = None
        if self.turn_timer:
            self.turn_timer.cancel()
        self.turn_timer = None
        if self.join_task:
            self.join_task.cancel()
        self.join_task = None
        self.missed_turns = {}

    @commands.Cog.listener()
    async def on_ready(self):
        print("EmpireGame Cog is ready.")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        await self.reset_game()

async def setup(bot: Red):
    if bot.get_cog('EmpireGame') is None:
        cog = EmpireGame(bot)
        await bot.add_cog(cog)
        try:
            bot.tree.add_command(cog.save_alias)
            bot.tree.add_command(cog.guess_alias)
        except discord.app_commands.CommandAlreadyRegistered:
            pass
