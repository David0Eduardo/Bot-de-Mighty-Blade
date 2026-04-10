
import discord
from discord.ext import commands
import re
import random
from utils import (
    carregar_dados,
    salvar_dados,
    editar_e_substituir_arquivos,
    atualizar_ficha_jogador,
    atualizar_painel_mestre,
    eh_mestre,
)
import pdfs
import aiohttp
import io
import fitz


class ValorModal(discord.ui.Modal, title='Ajustar Atributo'):
    valor = discord.ui.TextInput(label='Quantidade', placeholder='Ex: 5 ou -10', min_length=1, max_length=6)

    def __init__(self, token_view, tipo):
        super().__init__()
        self.token_view = token_view
        self.tipo = tipo

    async def on_submit(self, interaction: discord.Interaction):
        try:
            valor_limpo = str(self.valor.value or "").strip()
            valor_limpo = valor_limpo.replace("−", "-").replace("–", "-").replace("—", "-")
            valor_limpo = valor_limpo.replace("+ ", "+").replace("- ", "-")
            val = int(valor_limpo)
            if self.tipo == "PV":
                if hasattr(self.token_view, "hp_atual"):
                    self.token_view.hp_atual = max(0, min(self.token_view.hp_max, self.token_view.hp_atual + val))
                    await self.token_view.mensagem_vinculada.edit(embed=self.token_view.gerar_embed(), view=self.token_view)
                elif hasattr(self.token_view, "hp"):
                    self.token_view.hp = self.token_view._ajustar_recurso(self.token_view.hp, val)
                    if getattr(self.token_view, "mensagem_vinculada", None):
                        await self.token_view.mensagem_vinculada.edit(embed=self.token_view.gerar_embed(), view=self.token_view)
                await interaction.response.send_message(f"PV ajustado em {val}!", ephemeral=True)
            else:
                if hasattr(self.token_view, "mp_atual"):
                    self.token_view.mp_atual = max(0, min(self.token_view.mp_max, self.token_view.mp_atual + val))
                    await self.token_view.mensagem_vinculada.edit(embed=self.token_view.gerar_embed(), view=self.token_view)
                elif hasattr(self.token_view, "mp"):
                    self.token_view.mp = self.token_view._ajustar_recurso(self.token_view.mp, val)
                    if getattr(self.token_view, "mensagem_vinculada", None):
                        await self.token_view.mensagem_vinculada.edit(embed=self.token_view.gerar_embed(), view=self.token_view)
                await interaction.response.send_message(f"PM ajustado em {val}!", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("Digite apenas números!", ephemeral=True)


class TokenMonstro(discord.ui.View):
    def __init__(self, nome, hp_max, mp_max, defesa):
        super().__init__(timeout=300)
        self.nome = nome
        self.hp_max = hp_max
        self.hp_atual = hp_max
        self.mp_max = mp_max
        self.mp_atual = mp_max
        self.defesa = defesa
        self.vivo = True
        self.esta_atingido = False
        self.mensagem_vinculada = None
        self.status = []

    def gerar_embed(self):
        if self.hp_atual <= 0:
            self.hp_atual = 0
            self.vivo = False
            cor = 0x2b2d31
            titulo = f"💀 {self.nome} (MORTO)"
        else:
            cor = 0xff4d4d
            titulo = f"Token: {self.nome}"

        embed = discord.Embed(title=titulo, color=cor)
        status_hp = "☠️ **DERROTADO**" if not self.vivo else f"{self.hp_atual}/{self.hp_max}"
        embed.add_field(name="❤️ PV", value=status_hp, inline=True)
        embed.add_field(name="✨ PM", value=f"{self.mp_atual}/{self.mp_max}", inline=True)
        embed.add_field(name="🛡️ Dificuldade (Def)", value=f"{self.defesa}", inline=True)

        if not self.vivo:
            embed.set_footer(text="Este monstro não pode mais agir.")

        return embed

    @discord.ui.button(label="Ajustar PV", style=discord.ButtonStyle.danger)
    async def btn_pv(self, interaction, button):
        await interaction.response.send_modal(ValorModal(self, "PV"))

    @discord.ui.button(label="Ajustar PM", style=discord.ButtonStyle.secondary)
    async def btn_pm(self, interaction, button):
        await interaction.response.send_modal(ValorModal(self, "PM"))

    @discord.ui.button(label="- PV", style=discord.ButtonStyle.danger)
    async def tirar_pv(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.hp_atual = max(0, self.hp_atual - 1)
        await interaction.response.edit_message(embed=self.gerar_embed(), view=self)

    @discord.ui.button(label="+ PV", style=discord.ButtonStyle.success)
    async def add_pv(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.hp_atual = min(self.hp_max, self.hp_atual + 1)
        await interaction.response.edit_message(embed=self.gerar_embed(), view=self)

    @discord.ui.button(label="- PM", style=discord.ButtonStyle.secondary)
    async def tirar_pm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.mp_atual = max(0, self.mp_atual - 1)
        await interaction.response.edit_message(embed=self.gerar_embed(), view=self)


class TokenJogador(discord.ui.View):
    def __init__(self, p_dados, uid):
        super().__init__(timeout=300)
        self.uid = uid
        self.nome = p_dados.get('nome', 'Herói')
        self.hp = str(p_dados.get('hp', '??'))
        self.mp = str(p_dados.get('mp', '??'))
        self.status = []
        self.mensagem_vinculada = None

    def _ajustar_recurso(self, valor_atual, delta):
        texto = str(valor_atual or '').strip()
        m = re.match(r'^\s*(\d+)\s*/\s*(\d+)\s*$', texto)
        if m:
            atual = int(m.group(1))
            maximo = int(m.group(2))
            novo = max(0, min(maximo, atual + delta))
            return f"{novo}/{maximo}"

        m = re.match(r'^\s*(\d+)\s*$', texto)
        if m:
            atual = int(m.group(1))
            novo = max(0, atual + delta)
            return str(novo)

        numeros = re.findall(r'\d+', texto)
        if numeros:
            atual = int(numeros[0])
            maximo = int(numeros[1]) if len(numeros) > 1 else None
            if maximo is not None:
                novo = max(0, min(maximo, atual + delta))
                return f"{novo}/{maximo}"
            return str(max(0, atual + delta))

        raise ValueError(f"Valor inválido para ajuste: {texto}")

    def gerar_embed(self):
        embed = discord.Embed(title=f"👤 {self.nome}", color=discord.Color.blue())
        embed.add_field(name="❤️ Vida", value=f"**{self.hp}**", inline=True)
        embed.add_field(name="✨ Mana", value=f"**{self.mp}**", inline=True)
        if self.status:
            embed.add_field(name="📋 Status", value=", ".join(self.status), inline=False)
        embed.set_footer(text="Use os botões para ajustar PV/PM.")
        return embed

    @discord.ui.button(label="Ajustar PV", style=discord.ButtonStyle.danger)
    async def btn_pv(self, interaction, button):
        if str(interaction.user.id) != str(self.uid):
            return await interaction.response.send_message("Este token não é seu.", ephemeral=True)
        await interaction.response.send_modal(ValorModal(self, "PV"))

    @discord.ui.button(label="Ajustar PM", style=discord.ButtonStyle.secondary)
    async def btn_pm(self, interaction, button):
        if str(interaction.user.id) != str(self.uid):
            return await interaction.response.send_message("Este token não é seu.", ephemeral=True)
        await interaction.response.send_modal(ValorModal(self, "PM"))


class Combate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tokens_ativos = {}
        self.mensagens_temporarias = {}
        self.ordens_ativas = {}
        self.nomes_batalha = {}
        self.threads_mestre = {}

    def _norm(self, texto):
        return re.sub(r"\s+", " ", str(texto or "").strip()).lower()


    def _buscar_token_por_nome(self, guild_id: int, nome_alvo: str):
        tokens = self.tokens_ativos.get(guild_id, {})
        alvo_norm = self._norm(nome_alvo)
        for nome, token in tokens.items():
            if self._norm(nome) == alvo_norm:
                return nome, token
        return None, None

    def _gerar_texto_ordem(self, guild_id: int):
        ordem = list(self.ordens_ativas.get(guild_id, []))
        nome_bat = self.nomes_batalha.get(guild_id, "Combate")
        linhas = [f"**⚔️ ORDEM DE AGIR: {nome_bat}**"]
        pos = 1

        for ent in sorted(ordem, key=lambda x: x["ini"], reverse=True):
            token = self.tokens_ativos.get(guild_id, {}).get(ent["nome"])
            if ent.get("tipo") == "m" and token and hasattr(token, "vivo") and not token.vivo:
                continue
            emoji = "🟢" if ent.get("tipo") == "p" else "🔴"
            linhas.append(f"`{pos}º` {emoji} {ent['nome']} (Ini: {ent['ini']})")
            pos += 1

        return "\n".join(linhas)

    async def _enviar_ordem_atualizada(self, guild_id: int, canal):
        texto = self._gerar_texto_ordem(guild_id)
        msg = await canal.send(texto)
        self.mensagens_temporarias.setdefault(str(guild_id), []).append(msg.id)
        return msg

    def _buscar_party_real(self, dados_campanha, gid: str, party_alvo: str):
        partys = dados_campanha.get("partys", {}).get(gid, {})
        alvo_norm = self._norm(party_alvo)
        for nome, membros in partys.items():
            if self._norm(nome) == alvo_norm:
                return nome, list(membros or [])
        return None, []

    async def _get_or_create_monster_thread(self, guild_id: int, canal):
        dados = carregar_dados()
        gid = str(guild_id)
        if "config" not in dados:
            dados["config"] = {}
        if gid not in dados["config"]:
            dados["config"][gid] = {}

        thread_id = dados["config"][gid].get("thread_monstros")
        if thread_id:
            try:
                thread = await self.bot.fetch_channel(int(thread_id))
                return thread
            except Exception:
                pass

        thread = await canal.create_thread(name="📖 Monstros Disponíveis", type=discord.ChannelType.private_thread)
        dados["config"][gid]["thread_monstros"] = thread.id
        salvar_dados(dados)
        return thread

    def rolar_pelo_cog(self, uid, nome_char, faces):
        return random.randint(1, faces)

    def _obter_mestre_role_id(self, dados_campanha, gid: str):
        return (
            dados_campanha.get("config", {}).get(gid, {}).get("mestre_role")
            or dados_campanha.get(gid, {}).get("mestre_role")
            or dados_campanha.get("mestre_role")
        )

    async def _adicionar_mestres_na_thread(self, thread, guild, mestre_role_id):
        if not mestre_role_id:
            return

        try:
            role = guild.get_role(int(mestre_role_id))
        except Exception:
            role = None

        if not role:
            return

        for membro in role.members:
            try:
                await thread.add_user(membro)
            except Exception:
                pass


    @commands.Cog.listener()
    async def on_tentativa_ataque(self, alvo, total, guild_id, canal):
        _nome_real, token = self._buscar_token_por_nome(guild_id, alvo)
        if not token:
            return await canal.send(f"❌ Alvo '{alvo}' não encontrado no combate ativo.")

        if not hasattr(token, "defesa"):
            return await canal.send(f"⚠️ O alvo **{alvo}** não possui defesa registrada para comparação.")

        acertou = total >= int(token.defesa)
        if hasattr(token, "esta_atingido"):
            token.esta_atingido = acertou

        if acertou:
            await canal.send(f"✅ Ataque em **{alvo}** acertou! ({total} vs Defesa {token.defesa})")
        else:
            await canal.send(f"❌ Ataque em **{alvo}** errou! ({total} vs Defesa {token.defesa})")

    @commands.Cog.listener()
    async def on_tentativa_dano(self, alvo, total, guild_id, canal):
        nome_real, token = self._buscar_token_por_nome(guild_id, alvo)
        if not token:
            return await canal.send(f"❌ Alvo '{alvo}' não encontrado no combate ativo.")

        morreu_agora = False

        if hasattr(token, "hp_atual"):
            estava_vivo = getattr(token, "vivo", True)
            token.hp_atual = max(0, int(token.hp_atual) - int(total))
            if token.hp_atual <= 0 and hasattr(token, "vivo"):
                token.vivo = False
                morreu_agora = estava_vivo
            if getattr(token, "mensagem_vinculada", None):
                await token.mensagem_vinculada.edit(embed=token.gerar_embed(), view=token)
        elif hasattr(token, "hp"):
            atual = int(token.hp)
            token.hp = str(max(0, atual - int(total)))
            if getattr(token, "mensagem_vinculada", None):
                await token.mensagem_vinculada.edit(embed=token.gerar_embed(), view=token)
        else:
            return await canal.send(f"⚠️ O alvo **{alvo}** não possui PV rastreável.")

        await canal.send(f"💥 **{alvo}** sofreu **{total}** de dano.")

        if morreu_agora:
            await canal.send(f"☠️ **{alvo}** foi morto!")
            await self._enviar_ordem_atualizada(guild_id, canal)

    async def extrair_dados_e_imagem(self, nome_alvo, guild_id, canal):
        try:
            thread = await self._get_or_create_monster_thread(guild_id, canal)
            async for message in thread.history(limit=200):
                if message.author.id != self.bot.user.id:
                    continue
                if f"monstro:{self._norm(nome_alvo)}" not in self._norm(message.content):
                    continue

                if message.embeds:
                    embed = message.embeds[0]
                    pv = int(re.search(r"\d+", embed.fields[0].value).group()) if embed.fields else 10
                    pm = int(re.search(r"\d+", embed.fields[1].value).group()) if len(embed.fields) > 1 else 10
                    defesa = int(re.search(r"\d+", embed.fields[2].value).group()) if len(embed.fields) > 2 else 10
                    img_bytes = None
                    if message.attachments:
                        try:
                            img_bytes = await message.attachments[0].read()
                        except Exception:
                            img_bytes = None
                    return {"img_bytes": img_bytes, "pv": pv, "pm": pm, "def": defesa}, True
        except Exception:
            pass

        url = pdfs.MANUAIS_URLS.get("Codex Monstrorum")
        if not url:
            return None, False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None, False
                    pdf_bytes = await resp.read()

            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            nome_busca = nome_alvo.strip().upper()

            for pagina in doc:
                dict_pag = pagina.get_text("dict")
                instancia_valida = None

                for bloco in dict_pag["blocks"]:
                    if "lines" not in bloco:
                        continue
                    for linha in bloco["lines"]:
                        for span in linha["spans"]:
                            if nome_busca in span["text"].upper() and span["size"] > 11:
                                instancia_valida = fitz.Rect(span["bbox"])
                                break
                        if instancia_valida:
                            break
                    if instancia_valida:
                        break

                if not instancia_valida:
                    continue

                area_leitura = fitz.Rect(instancia_valida.x0, instancia_valida.y0, instancia_valida.x0 + 350, instancia_valida.y0 + 200)
                texto_area = pagina.get_text("text", clip=area_leitura)

                pv_match = re.search(r"PV[:\s]*(\d+)", texto_area, re.IGNORECASE)
                pm_match = re.search(r"PM[:\s]*(\d+)", texto_area, re.IGNORECASE)
                esquiva_match = re.search(r"Esquiva\s*[:\)]*\s*(\d+)", texto_area, re.IGNORECASE)
                defesa_match = re.search(r"Defesa\s*[:\s]*(\d+)", texto_area, re.IGNORECASE)

                pv = int(pv_match.group(1)) if pv_match else 10
                pm = int(pm_match.group(1)) if pm_match else 10
                dificuldade = int(esquiva_match.group(1)) if esquiva_match else int(defesa_match.group(1)) if defesa_match else 10

                rect_final = None
                for ficha in pagina.get_drawings():
                    if ficha["rect"].contains(instancia_valida):
                        rect_final = ficha["rect"]
                        break

                if not rect_final:
                    col_x = 0 if instancia_valida.x0 < 300 else 300
                    rect_final = fitz.Rect(col_x + 10, instancia_valida.y0 - 20, col_x + 295, instancia_valida.y0 + 400)

                pix = pagina.get_pixmap(clip=rect_final + (-2, -2, 2, 2), matrix=fitz.Matrix(2, 2))
                img_bytes = pix.tobytes("png")

                await self._salvar_monstro_no_topico(guild_id, canal, nome_alvo, pv, pm, dificuldade, img_bytes)
                return {"img_bytes": img_bytes, "pv": pv, "pm": pm, "def": dificuldade}, True

            return None, False
        except Exception as e:
            print(f"Erro na extração: {e}")
            return None, False

    async def _salvar_monstro_no_topico(self, guild_id, canal, nome, pv, pm, defesa, img_bytes):
        thread = await self._get_or_create_monster_thread(guild_id, canal)
        embed = discord.Embed(title=f"🐉 {nome}", color=discord.Color.red())
        embed.add_field(name="❤️ PV", value=f"{pv}", inline=True)
        embed.add_field(name="✨ PM", value=f"{pm}", inline=True)
        embed.add_field(name="🛡️ Defesa", value=f"{defesa}", inline=True)

        file = discord.File(io.BytesIO(img_bytes), filename=f"{nome}.png") if img_bytes else None
        await thread.send(content=f"MONSTRO:{self._norm(nome)}", embed=embed, file=file)

    @commands.hybrid_command(name="encontro", description="Inicia um encontro de combate com monstros e party.")
    async def encontro(self, ctx, nome_bat: str, party_alvo: str = "", *, monstros: str = ""):
        await ctx.defer()

        nome_bat = (nome_bat or "").strip().strip('"').strip("'")
        party_alvo = (party_alvo or "").strip()
        monstros = (monstros or "").strip()

        if not nome_bat:
            return await ctx.interaction.followup.send("❌ Informe um nome válido para a batalha.")

        gid = str(ctx.guild.id)
        gid_int = ctx.guild.id
        dados_campanha = carregar_dados()

        self.tokens_ativos[gid_int] = {}
        self.mensagens_temporarias[gid] = []
        self.ordens_ativas[gid_int] = []
        self.nomes_batalha[gid_int] = nome_bat

        canal_publico = ctx.channel
        thread_mestre = await canal_publico.create_thread(name=f"Mestre: {nome_bat}", type=discord.ChannelType.private_thread)
        self.threads_mestre[gid_int] = thread_mestre.id
        await self._adicionar_mestres_na_thread(thread_mestre, ctx.guild, self._obter_mestre_role_id(dados_campanha, gid))
        ordem_iniciativa = []

        if party_alvo:
            party_real, membros_ids = self._buscar_party_real(dados_campanha, gid, party_alvo)
            if not party_real:
                partys_disponiveis = list(dados_campanha.get("partys", {}).get(gid, {}).keys())
                extras = f"\nParties disponíveis: {', '.join(partys_disponiveis)}" if partys_disponiveis else ""
                await ctx.interaction.followup.send(f"⚠️ Party '{party_alvo}' não encontrada.{extras}")
                membros_ids = []

            personagens = dados_campanha.get("personagens", {}).get(gid, {})
            membros_validos = []
            for uid in membros_ids:
                uid = str(uid)
                if uid in personagens:
                    membros_validos.append(uid)

            for uid in membros_validos:
                p = personagens.get(uid)
                if not p or not p.get("thread_id"):
                    continue

                try:
                    msg_ficha = ""
                    thread_id = p.get("thread_id")
                    if thread_id:
                        try:
                            thread_player = await self.bot.fetch_channel(int(thread_id))
                            async for message in thread_player.history(limit=25, oldest_first=True):
                                conteudo = message.content or ""
                                if "FICHA DE PERSONAGEM:" in conteudo.upper():
                                    msg_ficha = conteudo
                                    break
                        except Exception as e:
                            print(f"Erro ao ler thread do player {p.get('nome', uid)}: {e}")

                    m_ini = re.search(r"Iniciativa:\s*(\d+)d(\d+)([\+\-]\d+)?", msg_ficha, re.I) if msg_ficha else None
                    if m_ini:
                        qtd, faces = int(m_ini.group(1)), int(m_ini.group(2))
                        bonus = int(m_ini.group(3) or 0)
                        valor_ini = sum(self.rolar_pelo_cog(uid, p['nome'], faces) for _ in range(qtd)) + bonus
                    else:
                        valor_ini = self.rolar_pelo_cog(uid, p['nome'], 20)

                    view_p = TokenJogador({"nome": p.get('nome', 'Herói'), "hp": p.get('hp', '0'), "mp": p.get('mp', '0')}, uid)
                    ordem_iniciativa.append({"nome": p.get('nome', f'Player {uid}'), "ini": valor_ini, "tipo": "p"})
                    msg_player = await canal_publico.send(embed=view_p.gerar_embed(), view=view_p)
                    view_p.mensagem_vinculada = msg_player
                    self.mensagens_temporarias[gid].append(msg_player.id)
                    self.tokens_ativos[gid_int][p.get('nome', f'Player {uid}')] = view_p

                except Exception as e:
                    print(f"Erro ao processar player {p.get('nome', uid)}: {e}")

        if monstros:
            padrao_monstros = r'(\d+)?\s*(?:"([^"]+)"|([a-zA-Zá-úÁ-Ú0-9\- ]+))\s*(\d+d\d+)?'
            matches = re.findall(padrao_monstros, monstros)

            for qtd_s, aspas, sem_aspas, dado_s in matches:
                m_nome = (aspas if aspas else sem_aspas).strip()
                if not m_nome:
                    continue

                qtd = int(qtd_s) if qtd_s else 1
                faces_ini = int(dado_s.split('d')[1]) if dado_s and 'd' in dado_s else 20
                dados_m, achou = await self.extrair_dados_e_imagem(m_nome, gid_int, canal_publico)
                if not achou:
                    continue

                file_f = discord.File(fp=io.BytesIO(dados_m["img_bytes"]), filename=f"{m_nome}.png") if dados_m["img_bytes"] else None
                await thread_mestre.send(content=f"📑 **Ficha de Espécie:** {m_nome}", file=file_f)

                for i in range(1, qtd + 1):
                    nome_un = f"{m_nome} {i}" if qtd > 1 else m_nome
                    rolagem_ini = random.randint(1, faces_ini)
                    ordem_iniciativa.append({"nome": nome_un, "ini": rolagem_ini, "tipo": "m"})

                    view_m = TokenMonstro(nome_un, dados_m["pv"], dados_m["pm"], dados_m["def"])
                    msg_m = await thread_mestre.send(embed=view_m.gerar_embed(), view=view_m)
                    view_m.mensagem_vinculada = msg_m
                    self.tokens_ativos[gid_int][nome_un] = view_m

        ordem_iniciativa.sort(key=lambda x: x["ini"], reverse=True)
        self.ordens_ativas[gid_int] = list(ordem_iniciativa)
        txt_ordem = self._gerar_texto_ordem(gid_int)

        await ctx.interaction.followup.send(f"✅ Combate `{nome_bat}` gerado com sucesso!")
        msg_ordem = await canal_publico.send(txt_ordem)
        self.mensagens_temporarias[gid].append(msg_ordem.id)

    @commands.hybrid_command(name="encontro_fim", description="Finaliza o encontro de combate e salva alterações nos personagens.")
    async def encontro_fim(self, ctx, nome_bat: str):
        await ctx.defer()
        gid = str(ctx.guild.id)
        gid_int = ctx.guild.id

        try:
            if gid_int not in self.tokens_ativos:
                return await ctx.interaction.followup.send("❌ Não há um combate ativo registrado.")

            tokens_batalha = self.tokens_ativos[gid_int]
            dados_globais = carregar_dados()
            log_salvamento = []

            for _nome_obj, view in tokens_batalha.items():
                if hasattr(view, 'uid'):
                    uid = str(view.uid)
                    p = dados_globais.get("personagens", {}).get(gid, {}).get(uid)

                    if p:
                        p["hp"] = str(view.hp)
                        p["mp"] = str(view.mp)

                        try:
                            await atualizar_ficha_jogador(ctx, uid, dados_globais)
                            log_salvamento.append(f"✅ **{p['nome']}** salvo.")
                        except Exception as e:
                            print(f"Erro ao salvar {p['nome']}: {e}")
                            log_salvamento.append(f"⚠️ **{p['nome']}**: Erro ao atualizar ficha.")

            salvar_dados(dados_globais)

            try:
                await atualizar_painel_mestre(ctx, dados_globais)
            except Exception as e:
                print(f"Erro ao atualizar painel do mestre: {e}")

            thread_mestre_id = self.threads_mestre.pop(gid_int, None)
            self.tokens_ativos.pop(gid_int, None)
            self.ordens_ativas.pop(gid_int, None)
            self.nomes_batalha.pop(gid_int, None)

            mensagens_ids = self.mensagens_temporarias.pop(gid, [])
            for msg_id in mensagens_ids:
                try:
                    msg = await ctx.channel.fetch_message(msg_id)
                    await msg.delete()
                except Exception:
                    pass

            thread_mestre = None
            if thread_mestre_id:
                try:
                    thread_mestre = await self.bot.fetch_channel(int(thread_mestre_id))
                except Exception as e:
                    print(f"Erro ao buscar thread do mestre por ID: {e}")

            if thread_mestre is None:
                try:
                    thread_mestre = discord.utils.get(ctx.channel.threads, name=f"Mestre: {nome_bat}")
                except Exception:
                    thread_mestre = None

            if thread_mestre:
                try:
                    await thread_mestre.delete()
                except discord.Forbidden as e:
                    print(f"Erro ao deletar thread do mestre: {e}")
                    try:
                        await thread_mestre.edit(archived=True, locked=True, name=f"[ENCERRADO] {thread_mestre.name}")
                    except Exception as e2:
                        print(f"Erro ao arquivar thread do mestre: {e2}")
                except Exception as e:
                    print(f"Erro ao deletar thread do mestre: {e}")

            embed = discord.Embed(
                title=f"⚔️ Fim de Combate: {nome_bat}",
                description="\n".join(log_salvamento) if log_salvamento else "Sem dados de jogadores para salvar.",

                color=0x2b2d31
            )
            await ctx.interaction.followup.send(embed=embed)
        except Exception as e:
            print(f"Erro em encontro_fim: {e}")
            await ctx.interaction.followup.send(f"❌ Erro ao finalizar combate: {e}")


async def setup(bot):
    await bot.add_cog(Combate(bot))