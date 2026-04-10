import discord
from discord.ext import commands
from discord import app_commands
import utils

class Config(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- COMANDO DE BARRA (SLASH COMMAND) ---
    @app_commands.command(
        name="setfichas", 
        description="Define o canal onde os tópicos de ficha estão localizados"
    )
    @app_commands.describe(canal="O canal de texto que contém os tópicos das fichas")
    @app_commands.checks.has_permissions(administrator=True)
    async def setfichas_slash(self, interaction: discord.Interaction, canal: discord.TextChannel):
        """Versão Slash do comando para definir o canal de fichas."""
        self._salvar_config_canal(interaction.guild_id, canal.id)
        await interaction.response.send_message(
            f"✅ **Configuração Salva!**\nAgora buscarei os tópicos de ficha em: {canal.mention}",
            ephemeral=True # Apenas quem usou o comando vê a resposta
        )

    # --- COMANDO DE PREFIXO (MANTIDO PARA COMPATIBILIDADE) ---
    @commands.command(name="setfichas", aliases=["set_fichas"])
    @commands.has_permissions(administrator=True)
    async def setfichas_prefix(self, ctx, canal: discord.TextChannel = None):
        """Versão de prefixo (!) do comando."""
        alvo = canal or ctx.channel
        self._salvar_config_canal(ctx.guild.id, alvo.id)
        await ctx.send(f"✅ **Canal de fichas definido!**\nLocal: {alvo.mention}")

    @app_commands.command(
        name="ver_configs", 
        description="Exibe as configurações atuais do servidor"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def ver_configs_slash(self, interaction: discord.Interaction):
        """Versão Slash para ver configurações."""
        dados = utils.carregar_dados()
        gid = str(interaction.guild_id)
        config = dados.get("config", {}).get(gid, {})
        
        embed = discord.Embed(title="⚙️ Configurações do Servidor", color=discord.Color.blue())
        embed.add_field(name="Canal de Fichas", value=f"<#{config.get('canal_fichas', 'Não definido')}>", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Config(bot))