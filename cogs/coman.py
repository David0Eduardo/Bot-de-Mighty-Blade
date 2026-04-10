
import discord
from discord import app_commands
from discord.ext import commands
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import io
import openpyxl
import pdfs
import aiohttp
import fitz
from utils import carregar_dados, salvar_dados


class MapSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=nome, description=f"Ver o {nome}")
            for nome in pdfs.MAPAS_URLS.keys()
        ]
        super().__init__(placeholder="Escolha um mapa para visualizar...", options=options)

    async def callback(self, interaction: discord.Interaction):
        link_mapa = pdfs.MAPAS_URLS.get(self.values[0])

        embed = discord.Embed(
            title=f"🗺️ {self.values[0]}",
            color=discord.Color.dark_green()
        )
        embed.set_image(url=link_mapa)
        embed.set_footer(text=f"Solicitado por {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed)


class MapView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(MapSelect())


class Util(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.base_dir = self._discover_base_dir()
        self.default_search_order = [
            "Manual MB",
            "Pequenas Magias",
            "Tebryn",
            "Bardo",
            "Linguas",
            "Guia do Heroi",
            "Guia do Vilao",
            "Planilha",
        ]
        self.item_search_ignored_columns = {
            "observacoes",
            "observação",
            "descricao",
            "descrição",
            "descricoes",
            "descrições",
        }

    def _discover_base_dir(self) -> Path:
        candidatos = [
            Path(__file__).resolve().parent,
            Path(__file__).resolve().parent.parent,
        ]

        for base in candidatos:
            if (base / "database.json").exists() or (base / "planilha_geral.xlsx").exists() or (base / "arquivos").exists():
                return base

        return Path(__file__).resolve().parent

    def _normalize(self, texto: Any) -> str:
        texto = "" if texto is None else str(texto)
        texto = unicodedata.normalize("NFKD", texto)
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", texto).strip().lower()

    def _clip(self, texto: Any, limite: int = 1024) -> str:
        texto = "" if texto is None else str(texto)
        texto = texto.strip()
        if len(texto) <= limite:
            return texto or "—"
        return texto[: limite - 3].rstrip() + "..."

    def _get_hab_value(self, habilidade: Dict[str, Any], key: str):
        return (
            habilidade.get(key)
            or habilidade.get(key.lower())
            or habilidade.get(key.capitalize())
            or habilidade.get(key.upper())
            or ""
        )

    async def _send_followup(self, ctx, **kwargs):
        if getattr(ctx, "interaction", None):
            return await ctx.interaction.followup.send(**kwargs)
        return await ctx.send(**kwargs)

    def _database_path(self) -> Path:
        return self.base_dir / "database.json"

    def _planilha_path(self) -> Path:
        return self.base_dir / "planilha_geral.xlsx"

    def _arquivos_dir(self) -> Path:
        return self.base_dir / "arquivos"

    def _extract_root_folder(self, arquivo: Path, base_dir: Optional[Path] = None) -> str:
        base = base_dir or self._arquivos_dir()
        try:
            relativo = arquivo.relative_to(base)
            return relativo.parts[0] if relativo.parts else arquivo.stem
        except Exception:
            return arquivo.stem

    def _folder_priority(self, pasta_raiz: str) -> int:
        pasta_norm = self._normalize(pasta_raiz)
        for i, nome in enumerate(self.default_search_order):
            if self._normalize(nome) == pasta_norm:
                return i
        return len(self.default_search_order) + 100

    def _split_long_text(self, texto: str, limite: int = 3800) -> List[str]:
        texto = (texto or "").strip()
        if not texto:
            return ["—"]

        partes = []
        atual = ""

        for bloco in texto.split("\n\n"):
            bloco = bloco.strip()
            if not bloco:
                continue

            candidato = bloco if not atual else f"{atual}\n\n{bloco}"
            if len(candidato) <= limite:
                atual = candidato
                continue

            if atual:
                partes.append(atual)
                atual = ""

            if len(bloco) <= limite:
                atual = bloco
                continue

            linhas = bloco.splitlines()
            sub = ""
            for linha in linhas:
                candidato_linha = linha if not sub else f"{sub}\n{linha}"
                if len(candidato_linha) <= limite:
                    sub = candidato_linha
                else:
                    if sub:
                        partes.append(sub)
                    sub = linha[:limite]
            if sub:
                atual = sub

        if atual:
            partes.append(atual)

        return partes or ["—"]

    async def _send_embed_in_chunks(self, ctx, title: str, body: str, color: int = 0x3498db, footer: Optional[str] = None):
        partes = self._split_long_text(body, 3800)
        for i, parte in enumerate(partes, 1):
            embed = discord.Embed(
                title=title if len(partes) == 1 else f"{title} ({i}/{len(partes)})",
                description=parte,
                color=color
            )
            if footer and i == len(partes):
                embed.set_footer(text=footer)
            await self._send_followup(ctx, embed=embed)

    def _iter_habilidades(self, data: Any, trilha: Optional[List[str]] = None):
        if trilha is None:
            trilha = []

        if isinstance(data, dict):
            nome = self._get_hab_value(data, "nome")
            descricao = self._get_hab_value(data, "descricao")
            if nome and descricao:
                yield data, trilha

            for chave, valor in data.items():
                nova_trilha = trilha + [str(chave)]
                yield from self._iter_habilidades(valor, nova_trilha)

        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    rotulo = item.get("Nome") or item.get("nome")
                    nova_trilha = trilha + ([str(rotulo)] if rotulo else [])
                    yield from self._iter_habilidades(item, nova_trilha)
                else:
                    yield from self._iter_habilidades(item, trilha)

    def _load_database(self) -> Dict[str, Any]:
        with open(self._database_path(), "r", encoding="utf-8") as f:
            return json.load(f)

    def _search_habilidades(self, termo: str) -> Tuple[List[Tuple[Dict[str, Any], List[str]]], List[Tuple[Dict[str, Any], List[str]]]]:
        database = self._load_database()
        raiz = database.get("habilidades", {})
        termo_norm = self._normalize(termo)

        exatas: List[Tuple[Dict[str, Any], List[str]]] = []
        parciais: List[Tuple[Dict[str, Any], List[str]]] = []
        vistos = set()

        for hab, trilha in self._iter_habilidades(raiz):
            nome = self._get_hab_value(hab, "nome")
            nome_norm = self._normalize(nome)
            if not nome_norm:
                continue

            chave = (nome_norm, tuple(trilha))
            if chave in vistos:
                continue
            vistos.add(chave)

            if nome_norm == termo_norm:
                exatas.append((hab, trilha))
            elif termo_norm in nome_norm:
                parciais.append((hab, trilha))

        return exatas, parciais

    def _extrair_metadados_habilidade(self, trilha: List[str]) -> Dict[str, str]:
        pasta_raiz = trilha[0] if trilha else "—"
        grupo = "—"
        origem = "—"
        secao = "—"

        for item in trilha[1:]:
            item_norm = self._normalize(item)
            if item_norm in {"classes", "caminhos", "racas", "raças", "habilidades gerais"} and grupo == "—":
                grupo = item
            elif item_norm in {
                "habilidade automatica",
                "habilidade automática",
                "habilidades basicas",
                "habilidades básicas",
                "habilidades avancadas",
                "habilidades avançadas",
                "habilidades finais",
                "habilidades extras",
            } and secao == "—":
                secao = item
            elif origem == "—":
                origem = item

        contexto = [f"Pasta-raiz: {pasta_raiz}"]
        if grupo != "—":
            contexto.append(f"Grupo: {grupo}")
        if origem != "—":
            contexto.append(f"Origem: {origem}")
        if secao != "—":
            contexto.append(f"Seção: {secao}")

        return {
            "pasta_raiz": pasta_raiz,
            "grupo": grupo,
            "origem": origem,
            "secao": secao,
            "contexto": " | ".join(contexto),
        }

    def _formatar_contexto_habilidade(self, trilha: List[str]) -> str:
        return self._extrair_metadados_habilidade(trilha)["contexto"]



    def _data_file_path(self) -> Path:
        return self.base_dir / "campanha_data.json"

    def _find_player_thread_id(self, guild_id: int, user_id: int) -> Optional[int]:
        try:
            dados = carregar_dados()
            return dados.get("personagens", {}).get(str(guild_id), {}).get(str(user_id), {}).get("thread_id")
        except Exception:
            return None

    async def _fetch_player_thread(self, ctx) -> Optional[discord.Thread]:
        thread_id = self._find_player_thread_id(ctx.guild.id, ctx.author.id)
        if thread_id:
            try:
                channel = await ctx.guild.fetch_channel(int(thread_id))
                if isinstance(channel, discord.Thread):
                    return channel
            except Exception:
                pass

        if isinstance(ctx.channel, discord.Thread):
            return ctx.channel
        return None

    async def _find_ficha_message(self, thread: discord.Thread) -> Optional[discord.Message]:
        async for message in thread.history(limit=50, oldest_first=True):
            if "🛡️ **FICHA DE PERSONAGEM:" in (message.content or ""):
                return message
        return None

    async def _find_backup_message(self, thread: discord.Thread) -> Optional[discord.Message]:
        async for message in thread.history(limit=50):
            if "SISTEMA_BACKUP_ID" in (message.content or "") and message.attachments:
                return message
        return None

    def _decrement_inventory_text_block(self, block: str, nome_item: str, quantidade: int) -> Tuple[str, bool]:
        linhas = block.splitlines()
        alvo_norm = self._normalize(nome_item)
        atualizado = False
        novas = []

        for linha in linhas:
            linha_limpa = linha.strip()
            if not linha_limpa:
                continue

            m = re.match(r"^(.*?)\s*\(x(\d+)\)\s*$", linha_limpa)
            if m:
                nome_atual = m.group(1).strip()
                qtd_atual = int(m.group(2))
                if self._normalize(nome_atual) == alvo_norm and not atualizado:
                    nova_qtd = qtd_atual - quantidade
                    atualizado = True
                    if nova_qtd > 0:
                        novas.append(f"{nome_atual} (x{nova_qtd})")
                    continue

            novas.append(linha_limpa)

        return ("\n".join(novas), atualizado)

    def _remove_item_from_ficha_message(self, content: str, nome_item: str, quantidade: int) -> Tuple[str, bool]:
        padrao = r"(\*\*INVENTÁRIO:\*\*\n)(.*)$"
        m = re.search(padrao, content, flags=re.S)
        if not m:
            return content, False

        bloco_atual = m.group(2).strip()
        bloco_novo, atualizado = self._decrement_inventory_text_block(bloco_atual, nome_item, quantidade)
        if not atualizado:
            return content, False

        novo_content = content[:m.start()] + m.group(1) + (bloco_novo if bloco_novo else "—")
        return novo_content, True

    def _remove_item_from_txt(self, raw_txt: str, nome_item: str, quantidade: int) -> Tuple[str, bool]:
        alvo_norm = self._normalize(nome_item)
        padrao = r"(Equipamentos[^\n]*\n)(.*?)(?=\n\n|----------|\Z)"
        m = re.search(padrao, raw_txt, flags=re.S | re.I)
        if not m:
            return raw_txt, False

        cabecalho = m.group(1)
        bloco = m.group(2).rstrip("\n")
        linhas = bloco.splitlines()
        atualizado = False
        novas = []

        for linha in linhas:
            original = linha.rstrip("\n")
            stripped = original.strip()
            if not stripped:
                continue

            nome_match = re.search(r"^\.(.*?)(?:\s{2,}|x\d+)", stripped)
            qtd_match = re.search(r"x(\d+)", stripped)

            if nome_match and qtd_match and not atualizado:
                nome_atual = nome_match.group(1).strip()
                qtd_atual = int(qtd_match.group(1))
                if self._normalize(nome_atual) == alvo_norm:
                    nova_qtd = qtd_atual - quantidade
                    atualizado = True
                    if nova_qtd > 0:
                        nova_linha = re.sub(r"x\d+", f"x{nova_qtd}", original, count=1)
                        novas.append(nova_linha)
                    continue

            novas.append(original)

        if not atualizado:
            return raw_txt, False

        bloco_novo = "\n".join(novas)
        novo_txt = raw_txt[:m.start()] + cabecalho + bloco_novo + raw_txt[m.end():]
        return novo_txt, True

    async def _save_updated_backup_txt(self, thread: discord.Thread, raw_txt: str, fallback_name: str = "personagem") -> bool:
        backup = await self._find_backup_message(thread)
        if not backup:
            return False

        filename = fallback_name + ".txt"
        for att in backup.attachments:
            if att.filename.lower().endswith(".txt"):
                filename = att.filename
                break

        try:
            await backup.delete()
        except Exception:
            pass

        await thread.send(
            content="💾 **BACKUP ATUALIZADO:**\n`SISTEMA_BACKUP_ID`",
            files=[discord.File(io.BytesIO(raw_txt.encode("utf-8")), filename=filename)]
        )
        return True


    def _check_master_permission(self, ctx) -> bool:
        try:
            dados = carregar_dados()
            gid = str(ctx.guild.id)
            role_id = dados.get("config", {}).get(gid, {}).get("mestre_role") or dados.get("config", {}).get(gid, {}).get("cargo_mestre")
            eh_admin = ctx.author.guild_permissions.administrator
            eh_role = role_id and any(r.id == int(role_id) for r in getattr(ctx.author, "roles", []))
            return bool(eh_admin or eh_role)
        except Exception:
            return False

    async def _buscar_pdf_bytes(self, nome_manual: str) -> Optional[bytes]:
        url = getattr(pdfs, "MANUAIS_URLS", {}).get(nome_manual)
        if not url:
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.read()
        except Exception:
            return None

    def _parse_first_int(self, texto: str, default: int = 0) -> int:
        m = re.search(r"-?\d+", str(texto or ""))
        return int(m.group()) if m else default

    def _extract_section_lines(self, texto: str, inicio: str, proximos: List[str]) -> List[str]:
        padrao = rf"{inicio}\s*\n(.*?)(?=\n(?:{'|'.join(proximos)})\b|\Z)"
        m = re.search(padrao, texto, flags=re.I | re.S)
        if not m:
            return []
        linhas = []
        for linha in m.group(1).splitlines():
            linha = linha.rstrip()
            if linha.strip():
                linhas.append(linha)
        return linhas

    def _parse_pet_data_from_text(self, nome_especie: str, texto: str) -> Optional[Dict[str, Any]]:
        if self._normalize(nome_especie) not in self._normalize(texto):
            return None

        def buscar_num(padroes: List[str], default: int = 0) -> int:
            for padrao in padroes:
                m = re.search(padrao, texto, flags=re.I)
                if m:
                    return int(m.group(1))
            return default

        dados = {
            "vida": buscar_num([r"\bPV[:\s]*(\d+)", r"Vida[:\s]*(\d+)", r"Pontos de Vida[:\s]*(\d+)"], 10),
            "mana": buscar_num([r"\bPM[:\s]*(\d+)", r"Mana[:\s]*(\d+)", r"Pontos de Mana[:\s]*(\d+)"], 0),
            "forca": buscar_num([r"For[cç]a[:\s]*(\d+)"], 0),
            "agilidade": buscar_num([r"Agilidade[:\s]*(\d+)"], 0),
            "inteligencia": buscar_num([r"Intelig[eê]ncia[:\s]*(\d+)"], 0),
            "vontade": buscar_num([r"Vontade[:\s]*(\d+)"], 0),
            "bloqueio": buscar_num([r"Bloqueio[:\s]*(\d+)"], 0),
            "esquiva": buscar_num([r"Esquiva[:\s]*(\d+)"], 0),
            "determinacao": buscar_num([r"Determina[cç][aã]o[:\s]*(\d+)"], 0),
        }

        ataques = self._extract_section_lines(texto, r"Ataques", ["Habilidades", "Poderes", "Descrição", "Descricao"])
        habilidades = self._extract_section_lines(texto, r"Habilidades", ["Poderes", "Descrição", "Descricao", "Ataques"])

        ataques = [l if l.lstrip().startswith(".") else f".{l.strip()}" for l in ataques]
        habilidades = [l if l.lstrip().startswith(".") else f".{l.strip()}" for l in habilidades]

        if not ataques:
            ataques = [".Ataque +0          Contusão         CaC   0"]
        if not habilidades:
            habilidades = [".Tipo: Desconhecido               [S]   -    ."]

        dados["ataques"] = ataques
        dados["habilidades"] = habilidades
        return dados

    async def _buscar_pet_no_codex(self, nome_especie: str) -> Optional[Dict[str, Any]]:
        pdf_bytes = await self._buscar_pdf_bytes("Codex Monstrorum")
        if not pdf_bytes:
            return None

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception:
            return None

        alvo_norm = self._normalize(nome_especie)
        melhor = None

        for pagina in doc:
            texto = pagina.get_text("text")
            texto_norm = self._normalize(texto)
            if alvo_norm not in texto_norm:
                continue

            dados = self._parse_pet_data_from_text(nome_especie, texto)
            if dados:
                melhor = dados
                break

        return melhor

    def _format_pet_block(self, nome_pet: str, nome_especie: str, dados_pet: Dict[str, Any]) -> str:
        ataques = dados_pet.get("ataques", [])
        habilidades = dados_pet.get("habilidades", [])

        linhas = [
            f"{nome_pet} ({nome_especie})",
            f".Vida: {dados_pet.get('vida', 0)}",
            f".Mana: {dados_pet.get('mana', 0)}",
            "",
            "Atributos:",
            f".Força:        {dados_pet.get('forca', 0)}",
            f".Agilidade:    {dados_pet.get('agilidade', 0)}",
            f".Inteligência: {dados_pet.get('inteligencia', 0)}",
            f".Vontade:      {dados_pet.get('vontade', 0)}",
            "",
            "Defesas:",
            f".Bloqueio:     {dados_pet.get('bloqueio', 0)}",
            f".Esquiva:      {dados_pet.get('esquiva', 0)}",
            f".Determinação: {dados_pet.get('determinacao', 0)}",
            "",
            "Ataques             tipo             alc. dano ",
        ]
        linhas.extend(ataques)
        linhas.append("")
        linhas.append("Habilidades                       tipo mana desc.")
        linhas.extend(habilidades)
        return "\n".join(linhas).rstrip() + "\n"

    def _append_pet_to_txt(self, raw_txt: str, pet_block: str) -> str:
        base = raw_txt.rstrip()
        return base + "\n\n----------\n\n" + pet_block

    async def _append_pet_to_thread(self, thread: discord.Thread, pet_block: str):
        await thread.send("**▬▬▬▬▬▬▬▬▬▬ ALIADOS & PETS ▬▬▬▬▬▬▬▬▬▬**")
        for parte in self._split_long_text(pet_block, 1800):
            await thread.send(parte)

    @commands.hybrid_command(name="map", description="Exibe um menu para visualizar mapas disponíveis do jogo.")
    async def map(self, ctx):
        if not pdfs.MAPAS_URLS:
            return await ctx.send("❌ Nenhum mapa configurado.", ephemeral=True)
        await ctx.send("Selecione um mapa:", view=MapView(), ephemeral=True)

    @commands.hybrid_command(name="clear", description="Limpa mensagens não fixadas do canal (até 100).")
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx):
        await ctx.defer(ephemeral=True)
        def check_fixada(m):
            return not m.pinned
        deleted = await ctx.channel.purge(limit=100, check=check_fixada)
        await self._send_followup(ctx, content=f"🧹 Limpeza concluída: {len(deleted)} mensagens removidas.")

    @commands.hybrid_command(name="hab", description="Busca informações de uma habilidade no banco de dados.")
    async def buscar_habilidade(self, ctx, *, nome_habilidade: str):
        await ctx.defer()

        if not self._database_path().exists():
            return await self._send_followup(ctx, content="❌ Banco de dados não encontrado.")

        try:
            exatas, parciais = self._search_habilidades(nome_habilidade)
        except json.JSONDecodeError:
            return await self._send_followup(ctx, content="❌ Erro ao ler o banco de dados.")
        except Exception as e:
            return await self._send_followup(ctx, content=f"❌ Erro ao buscar habilidade: {e}")

        if exatas:
            habilidade, trilha = exatas[0]
            return await self._formatar_habilidade(ctx, habilidade, trilha)

        if not parciais:
            return await self._send_followup(ctx, content=f"❌ Habilidade '{nome_habilidade}' não encontrada.")

        if len(parciais) == 1:
            habilidade, trilha = parciais[0]
            return await self._formatar_habilidade(ctx, habilidade, trilha)

        embed = discord.Embed(
            title=f"🔍 Múltiplas habilidades encontradas para '{nome_habilidade}'",
            color=0x3498db
        )

        linhas = []
        for i, (hab, trilha) in enumerate(parciais[:10], 1):
            nome = self._get_hab_value(hab, "nome") or "Sem nome"
            tipo = self._get_hab_value(hab, "tipo") or "Sem tipo"
            contexto = self._formatar_contexto_habilidade(trilha)
            linhas.append(f"{i}. **{nome}** ({tipo})\n↳ {self._clip(contexto, 140)}")

        embed.description = "\n\n".join(linhas)
        embed.set_footer(text="Use o nome exato para retornar direto a habilidade.")
        await self._send_followup(ctx, embed=embed)

    async def _formatar_habilidade(self, ctx, habilidade: Dict[str, Any], trilha: Optional[List[str]] = None):
        embed = discord.Embed(
            title=f"⚔️ {self._get_hab_value(habilidade, 'nome')}",
            color=0x2ecc71
        )

        if trilha:
            meta = self._extrair_metadados_habilidade(trilha)
            embed.add_field(name="Pasta-raiz", value=self._clip(meta["pasta_raiz"], 1024), inline=True)
            if meta["grupo"] != "—":
                embed.add_field(name="Grupo", value=self._clip(meta["grupo"], 1024), inline=True)
            if meta["origem"] != "—":
                embed.add_field(name="Origem", value=self._clip(meta["origem"], 1024), inline=True)
            if meta["secao"] != "—":
                embed.add_field(name="Seção", value=self._clip(meta["secao"], 1024), inline=True)
            embed.add_field(name="Caminho completo", value=self._clip(" > ".join(trilha), 1024), inline=False)

        for campo, titulo in [
            ("tipo", "Tipo"),
            ("subtipo", "Subtipo"),
            ("requisito", "Requisito"),
            ("mana", "Mana"),
            ("dificuldade", "Dificuldade"),
            ("racas", "Raças"),
        ]:
            valor = self._get_hab_value(habilidade, campo)
            if valor:
                embed.add_field(name=titulo, value=self._clip(valor, 1024), inline=True)

        descricao = self._get_hab_value(habilidade, "descricao")
        if descricao:
            embed.add_field(name="Descrição", value=self._clip(descricao, 1024), inline=False)

        especial = self._get_hab_value(habilidade, "especial")
        if especial:
            embed.add_field(name="Especial", value=self._clip(especial, 1024), inline=False)

        detalhes = (
            habilidade.get("detalhes_adicionais")
            or habilidade.get("Detalhes Adicionais")
            or []
        )
        if isinstance(detalhes, list) and detalhes:
            detalhes_str = "\n".join(f"• {str(x)}" for x in detalhes)
            embed.add_field(name="Detalhes Adicionais", value=self._clip(detalhes_str, 1024), inline=False)

        await self._send_followup(ctx, embed=embed)


    def _iter_txt_files(self, exclude_folders: Optional[str] = None, specific_folders: Optional[str] = None):
        arquivos_dir = self._arquivos_dir()
        if not arquivos_dir.exists():
            return

        exclude_list = [self._normalize(x) for x in exclude_folders.split(",")] if exclude_folders else []
        specific_list = [self._normalize(x) for x in specific_folders.split(",")] if specific_folders else []

        arquivos = []
        for arquivo in arquivos_dir.rglob("*.txt"):
            nome_lower = arquivo.name.lower()
            if nome_lower.startswith("hab_") or nome_lower.startswith("hab"):
                continue

            caminho_relativo = self._normalize(str(arquivo.relative_to(arquivos_dir)))
            pasta_raiz = self._extract_root_folder(arquivo, arquivos_dir)
            pasta_raiz_norm = self._normalize(pasta_raiz)

            if exclude_list and any(excl and (excl in caminho_relativo or excl == pasta_raiz_norm) for excl in exclude_list):
                continue

            if specific_list and not any(spec and (spec in caminho_relativo or spec == pasta_raiz_norm) for spec in specific_list):
                continue

            arquivos.append((arquivo, pasta_raiz))

        if specific_list:
            ordem_especifica = {nome: i for i, nome in enumerate(specific_list)}
            arquivos.sort(key=lambda x: (ordem_especifica.get(self._normalize(x[1]), 9999), str(x[0]).lower()))
        else:
            arquivos.sort(key=lambda x: (self._folder_priority(x[1]), str(x[0]).lower()))

        for arquivo, _ in arquivos:
            yield arquivo


    def _buscar_em_secao(self, termo: str, secao_texto: str, modo: str = "exact") -> Optional[Tuple[int, str]]:
        linhas = [linha.strip() for linha in secao_texto.splitlines() if linha.strip()]
        termo_norm = self._normalize(termo)
        modo = self._normalize(modo) or "exact"

        def confere(valor: str) -> bool:
            valor_norm = self._normalize(valor)
            if not valor_norm:
                return False
            if modo == "half":
                return termo_norm in valor_norm
            return valor_norm == termo_norm

        for linha in linhas:
            m = re.match(r"^\*\*(.+?)\*\*$", linha)
            if m and confere(m.group(1)):
                return 1, m.group(1).strip()

        for linha in linhas:
            m = re.match(r"^\*([^*:][^:]*):", linha)
            if m and confere(m.group(1)):
                return 2, m.group(1).strip()

        for linha in linhas:
            m = re.match(r"^-?\*([^*:][^:]*):", linha)
            if m and confere(m.group(1)):
                return 3, m.group(1).strip()

        return None

    @commands.hybrid_command(name="info", description="Busca informações nos arquivos txt seguindo ordem específica.")
    @app_commands.describe(
        ignorar_pastas="Pastas a excluir da busca (separadas por vírgula).",
        pastas="Buscar apenas nessas pastas (separadas por vírgula).",
        tipo="Use half para buscar por parte do texto; sem isso, busca exata."
    )
    async def buscar_informacao(self, ctx, termo_busca: str, ignorar_pastas: Optional[str] = None, pastas: Optional[str] = None, tipo: Optional[str] = None):
        await ctx.defer()

        arquivos_dir = self._arquivos_dir()
        if not arquivos_dir.exists():
            return await self._send_followup(ctx, content="❌ Diretório de arquivos não encontrado.")

        resultados = []
        max_resultados = 3

        try:
            for arquivo in self._iter_txt_files(ignorar_pastas, pastas):
                try:
                    conteudo = arquivo.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                secoes = re.split(r"(?m)^\s*---\s*$", conteudo)
                for secao in secoes:
                    secao_texto = secao.strip()
                    if not secao_texto:
                        continue

                    achado = self._buscar_em_secao(termo_busca, secao_texto, modo=tipo or "exact")
                    if not achado:
                        continue

                    prioridade, titulo = achado
                    pasta_raiz = self._extract_root_folder(arquivo, arquivos_dir)
                    resultados.append({
                        "prioridade": prioridade,
                        "titulo": titulo,
                        "secao": secao_texto,
                        "arquivo": arquivo.name,
                        "caminho": str(arquivo.relative_to(arquivos_dir)),
                        "pasta_raiz": pasta_raiz,
                        "ordem_pasta": self._folder_priority(pasta_raiz),
                    })

                if resultados and not pastas:
                    melhor_ordem = min(r["ordem_pasta"] for r in resultados)
                    if melhor_ordem < len(self.default_search_order):
                        break
        except Exception as e:
            return await self._send_followup(ctx, content=f"❌ Erro ao buscar informações: {e}")

        if not resultados:
            embed = discord.Embed(
                title="🔍 Informações não encontradas",
                description=f"Nenhuma informação encontrada para '{termo_busca}' nos arquivos txt.",
                color=0xe74c3c
            )
            return await self._send_followup(ctx, embed=embed)

        resultados.sort(key=lambda x: (x["prioridade"], x["ordem_pasta"], x["caminho"].lower()))
        resultados = resultados[:max_resultados]

        for i, resultado in enumerate(resultados, 1):
            titulo = f"📚 {i}. {resultado['titulo']} — {resultado['arquivo']}"
            cabecalho = (
                f"**Pasta-raiz:** {resultado['pasta_raiz']}\n"
                f"**Caminho:** {resultado['caminho']}\n\n"
            )
            corpo = cabecalho + resultado["secao"]
            await self._send_embed_in_chunks(
                ctx,
                titulo,
                corpo,
                color=0x3498db,
                footer="Ordem: **Título** > *Subtítulo:* > -*Detalhe:*"
            )


    def _search_planilha(self, termo: str, modo: str = "exact") -> List[Dict[str, Any]]:
        caminho = self._planilha_path()
        wb = openpyxl.load_workbook(caminho, data_only=True)
        termo_norm = self._normalize(termo)
        modo = self._normalize(modo) or "exact"
        resultados = []

        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue

            headers = [str(c).strip() if c is not None else "" for c in rows[0]]

            for idx, row in enumerate(rows[1:], start=2):
                valores = ["" if v is None else str(v).strip() for v in row]
                pares = [(header, valor) for header, valor in zip(headers, valores) if valor]
                if not pares:
                    continue

                partes_busca = []
                for header, valor in pares:
                    header_norm = self._normalize(header)
                    if header_norm in self.item_search_ignored_columns:
                        continue
                    partes_busca.append(valor)

                valores_norm = [self._normalize(valor) for valor in partes_busca if self._normalize(valor)]
                if not valores_norm:
                    continue

                if modo == "half":
                    encontrado = any(termo_norm in valor_norm for valor_norm in valores_norm)
                else:
                    encontrado = any(valor_norm == termo_norm for valor_norm in valores_norm)

                if not encontrado:
                    continue

                item_nome = next(
                    (valor for header, valor in pares if self._normalize(header) not in self.item_search_ignored_columns),
                    None
                ) or next((valor for _, valor in pares), "Sem nome")

                campos = [f"**{header or 'Campo'}:** {valor}" for header, valor in pares]

                resultados.append({
                    "aba": ws.title,
                    "linha": idx,
                    "item": item_nome,
                    "conteudo": "\n".join(campos),
                })

        return resultados


    @commands.hybrid_command(name="item", description="Busca informações de itens diretamente na planilha.")
    @app_commands.describe(tipo="Use half para buscar por parte do texto; sem isso, busca exata.")
    async def buscar_item(self, ctx, nome_item: str, tipo: Optional[str] = None):
        await ctx.defer()

        caminho = self._planilha_path()
        if not caminho.exists():
            return await self._send_followup(ctx, content="❌ Planilha não encontrada.")

        try:
            resultados = self._search_planilha(nome_item, modo=tipo or "exact")
        except Exception as e:
            return await self._send_followup(ctx, content=f"❌ Erro ao ler a planilha: {e}")

        if not resultados:
            embed = discord.Embed(
                title="🔍 Item não encontrado",
                description=f"Nenhum item correspondente a '{nome_item}' foi encontrado na planilha.",
                color=0xe74c3c
            )
            return await self._send_followup(ctx, embed=embed)

        embed = discord.Embed(
            title=f"📊 Resultados para '{nome_item}'",
            description=f"Foram encontrados {len(resultados)} resultado(s) na planilha.",
            color=0x3498db
        )

        for resultado in resultados[:8]:
            conteudo = f"**Pasta-raiz:** Planilha\n**Aba:** {resultado['aba']}\n**Linha:** {resultado['linha']}\n\n{resultado['conteudo']}"
            embed.add_field(
                name=f"{resultado['item']} — {resultado['aba']} (linha {resultado['linha']})",
                value=self._clip(conteudo, 1024),
                inline=False
            )

        if len(resultados) > 8:
            embed.set_footer(text=f"Mostrando 8 de {len(resultados)} resultados.")
        await self._send_followup(ctx, embed=embed)


    @commands.hybrid_command(name="usar_item", description="Remove um item do seu inventário, como se tivesse sido usado.")
    @app_commands.describe(quantidade="Quantidade a remover do inventário.")
    async def usar_item(self, ctx, nome_item: str, quantidade: Optional[int] = 1):
        await ctx.defer()

        if quantidade is None or quantidade <= 0:
            return await self._send_followup(ctx, content="❌ A quantidade deve ser maior que 0.")

        thread = await self._fetch_player_thread(ctx)
        if not thread:
            return await self._send_followup(ctx, content="❌ Não encontrei a thread da sua ficha.")

        ficha_msg = await self._find_ficha_message(thread)
        if not ficha_msg:
            return await self._send_followup(ctx, content="❌ Não encontrei a mensagem da ficha no seu tópico.")

        novo_content, atualizou_ficha = self._remove_item_from_ficha_message(ficha_msg.content or "", nome_item, quantidade)
        if not atualizou_ficha:
            return await self._send_followup(ctx, content=f"❌ O item '{nome_item}' não foi encontrado no seu inventário.")

        await ficha_msg.edit(content=novo_content)

        backup_msg = await self._find_backup_message(thread)
        if backup_msg:
            raw_txt = ""
            filename_base = "personagem"
            for att in backup_msg.attachments:
                if att.filename.lower().endswith(".txt"):
                    filename_base = Path(att.filename).stem
                    data = await att.read()
                    try:
                        raw_txt = data.decode("utf-8")
                    except Exception:
                        raw_txt = data.decode("latin-1")
                    break

            if raw_txt:
                novo_txt, atualizou_txt = self._remove_item_from_txt(raw_txt, nome_item, quantidade)
                if atualizou_txt:
                    await self._save_updated_backup_txt(thread, novo_txt, filename_base)

        embed = discord.Embed(
            title="🧪 Item usado",
            description=f"Item **{nome_item}** removido do inventário em **x{quantidade}**.",
            color=0x2ecc71
        )
        embed.set_footer(text="A ficha do tópico e o backup .txt foram atualizados.")
        await self._send_followup(ctx, embed=embed)


    @commands.hybrid_command(name="mestre_pet", description="Mestre adiciona um pet a um personagem usando dados do Codex ou valores manuais.")
    @app_commands.describe(
        nome_especie="Espécie do pet para buscar no Codex Monstrorum.",
        nome_pet="Nome próprio do pet.",
        vida="Opcional: sobrescrever Vida.",
        mana="Opcional: sobrescrever Mana.",
        forca="Opcional: sobrescrever Força.",
        agilidade="Opcional: sobrescrever Agilidade.",
        inteligencia="Opcional: sobrescrever Inteligência.",
        vontade="Opcional: sobrescrever Vontade.",
        bloqueio="Opcional: sobrescrever Bloqueio.",
        esquiva="Opcional: sobrescrever Esquiva.",
        determinacao="Opcional: sobrescrever Determinação."
    )
    async def mestre_pet(
        self,
        ctx,
        usuario: discord.Member,
        nome_especie: str,
        nome_pet: str,
        vida: Optional[int] = None,
        mana: Optional[int] = None,
        forca: Optional[int] = None,
        agilidade: Optional[int] = None,
        inteligencia: Optional[int] = None,
        vontade: Optional[int] = None,
        bloqueio: Optional[int] = None,
        esquiva: Optional[int] = None,
        determinacao: Optional[int] = None,
    ):
        await ctx.defer()

        if not self._check_master_permission(ctx):
            return await self._send_followup(ctx, content="❌ Apenas o mestre ou administrador pode usar este comando.")

        thread_id = self._find_player_thread_id(ctx.guild.id, usuario.id)
        if not thread_id:
            return await self._send_followup(ctx, content=f"❌ Não encontrei a thread da ficha de **{usuario.display_name}**.")

        try:
            thread = await ctx.guild.fetch_channel(int(thread_id))
        except Exception as e:
            return await self._send_followup(ctx, content=f"❌ Não foi possível abrir a thread da ficha: {e}")

        backup_msg = await self._find_backup_message(thread)
        if not backup_msg:
            return await self._send_followup(ctx, content="❌ Não encontrei o backup `.txt` da ficha.")

        raw_txt = ""
        filename_base = "personagem"
        for att in backup_msg.attachments:
            if att.filename.lower().endswith(".txt"):
                filename_base = Path(att.filename).stem
                data = await att.read()
                try:
                    raw_txt = data.decode("utf-8")
                except Exception:
                    raw_txt = data.decode("latin-1")
                break

        if not raw_txt:
            return await self._send_followup(ctx, content="❌ Não consegui ler o arquivo `.txt` da ficha.")

        dados_pet = await self._buscar_pet_no_codex(nome_especie)
        if not dados_pet:
            dados_pet = {
                "vida": 0, "mana": 0, "forca": 0, "agilidade": 0, "inteligencia": 0, "vontade": 0,
                "bloqueio": 0, "esquiva": 0, "determinacao": 0,
                "ataques": [".Ataque +0          Contusão         CaC   0"],
                "habilidades": [".Tipo: Desconhecido               [S]   -    ."]
            }

        overrides = {
            "vida": vida,
            "mana": mana,
            "forca": forca,
            "agilidade": agilidade,
            "inteligencia": inteligencia,
            "vontade": vontade,
            "bloqueio": bloqueio,
            "esquiva": esquiva,
            "determinacao": determinacao,
        }
        for chave, valor in overrides.items():
            if valor is not None:
                dados_pet[chave] = valor

        pet_block = self._format_pet_block(nome_pet, nome_especie, dados_pet)
        novo_txt = self._append_pet_to_txt(raw_txt, pet_block)
        await self._save_updated_backup_txt(thread, novo_txt, filename_base)
        await self._append_pet_to_thread(thread, pet_block)

        embed = discord.Embed(
            title="🐾 Pet adicionado",
            description=(
                f"**Personagem:** {usuario.mention}\n"
                f"**Pet:** {nome_pet}\n"
                f"**Espécie:** {nome_especie}\n"
                f"Os dados de atributos foram obtidos do Codex Monstrorum e sobrescritos pelos valores informados, quando enviados.\n"
                f"Ataques e Habilidades foram mantidos a partir do Codex."
            ),
            color=0x2ecc71
        )
        await self._send_followup(ctx, embed=embed)

    @commands.hybrid_command(name="dados_h", description="Mostra exemplos de uso do sistema de dados e ataques.")
    async def dados_help(self, ctx):
        embed = discord.Embed(
            title="🎲 Sistema de Dados - Ajuda",
            description="Sistema avançado de rolagens com carma, bônus separados e tipos de ataque.",
            color=0x2b2d31
        )

        embed.add_field(
            name="📋 Sintaxe Geral",
            value="• `atk(serve para verificar se o ataque acertou o monstro) [dados] [bônus] [atributo] [equip] [-p pet] / [alvo]`\n• `dano(da o dano no monstro que esta na ficha do combate) [dados] [bônus] [atributo] [equip] [-p pet] / [alvo]`",
            inline=False
        )

        embed.add_field(
            name="💪 Atributos (Players)",
            value="• `for` - Força\n• `agi` - Agilidade\n• `int` - Inteligência\n• `von` - Vontade",
            inline=True
        )

        embed.add_field(
            name="🐾 Atributos (Pets)",
            value="• `for` - FOR\n• `agi` - AGI\n• `int` - INT\n• `von` - VON",
            inline=True
        )

        embed.add_field(
            name="💥 Exemplos - Ataque de Dano",
            value="• `dano 3d8 +1 Espada / Goblin`\n• `dano 2d8 Bordão / Troll`\n• `dano 2d8 Mordida -p Trix / Troll`",
            inline=False
        )

        embed.add_field(
            name="🎲 Rolagens Simples",
            value="• `1d20` - Rolagem básica\n• `2d6 +3` - Com bônus manual\n• `3d8 for` - Com atributo\n• `1d100 Adaga` - Com equipamento",
            inline=False
        )

        embed.set_footer(text="💡 Dica: Use 'atk' para ataques que verificam acerto. O carma pode sorrir para você!")

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="help-rpg-mb", description="Exibe uma lista de todos os comandos slash disponíveis no bot.")
    async def help_rpg(self, ctx):
        await ctx.defer()

        comandos_tree = sorted(self.bot.tree.get_commands(), key=lambda c: c.name.lower())
        total = len(comandos_tree)

        embed_principal = discord.Embed(
            title="⚔️ Mighty Blade RPG - Lista de Comandos",
            description=(
                f"Atualmente existem **{total}** comandos sincronizados neste bot.\n"
            ),
            color=0x2f3136
        )

        embed_principal.set_footer(text="Use os comandos começando com / | Para busca parcial use tipo:half")
        await self._send_followup(ctx, embed=embed_principal)

        lista_comandos = []
        for cmd in comandos_tree:
            desc = cmd.description if cmd.description else "Sem descrição definida."
            lista_comandos.append(f"**/{cmd.name}**\n*{desc}*")

        texto = "\n\n".join(lista_comandos)
        await self._send_embed_in_chunks(
            ctx,
            "📜 Lista completa de comandos",
            texto,
            color=0x5865F2,
            footer="Lista automática dos comandos registrados no bot"
        )
    @commands.hybrid_command(name="arquivos", description="Lista pastas e arquivos dentro de arquivos/.")
    @app_commands.describe(pasta="Nome da pasta para listar conteúdo (opcional).")
    async def listar_arquivos(self, ctx, pasta: str = None):
        await ctx.defer()

        arquivos_dir = self._arquivos_dir()

        if not arquivos_dir.exists():
            return await self._send_followup(ctx, content="❌ Diretório de arquivos não encontrado.")

        if pasta is None:
            try:
                itens = list(arquivos_dir.iterdir())
                pastas = [item.name for item in itens if item.is_dir()]
                pastas.sort()

                if not pastas:
                    embed = discord.Embed(
                        title="📁 Pastas em arquivos/",
                        description="Nenhuma pasta encontrada.",
                        color=0xe74c3c
                    )
                else:
                    embed = discord.Embed(
                        title="📁 Pastas em arquivos/",
                        description="\n".join(f"• {pasta}" for pasta in pastas),
                        color=0x3498db
                    )
                    embed.set_footer(text=f"Total: {len(pastas)} pastas | Use /arquivos \"nome_da_pasta\" para ver conteúdo")
            except Exception as e:
                embed = discord.Embed(
                    title="❌ Erro",
                    description=f"Erro ao listar pastas: {e}",
                    color=0xe74c3c
                )
        else:
            pasta_dir = arquivos_dir / pasta
            if not pasta_dir.exists() or not pasta_dir.is_dir():
                embed = discord.Embed(
                    title="❌ Pasta não encontrada",
                    description=f"A pasta '{pasta}' não existe em arquivos/.",
                    color=0xe74c3c
                )
            else:
                try:
                    itens = list(pasta_dir.iterdir())
                    arquivos = [item.name for item in itens if item.is_file()]
                    subpastas = [item.name for item in itens if item.is_dir()]

                    arquivos.sort()
                    subpastas.sort()

                    description = ""
                    if subpastas:
                        description += f"**Subpastas:**\n" + "\n".join(f"📁 {sub}" for sub in subpastas) + "\n\n"
                    if arquivos:
                        description += f"**Arquivos:**\n" + "\n".join(f"📄 {arq}" for arq in arquivos)

                    if not description:
                        description = "Pasta vazia."

                    embed = discord.Embed(
                        title=f"📂 Conteúdo de arquivos/{pasta}/",
                        description=description,
                        color=0x3498db
                    )
                    embed.set_footer(text=f"Subpastas: {len(subpastas)} | Arquivos: {len(arquivos)}")
                except Exception as e:
                    embed = discord.Embed(
                        title="❌ Erro",
                        description=f"Erro ao listar conteúdo: {e}",
                        color=0xe74c3c
                    )

        await self._send_followup(ctx, embed=embed)


async def setup(bot):
    await bot.add_cog(Util(bot))
