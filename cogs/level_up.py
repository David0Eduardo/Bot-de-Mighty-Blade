
import discord
from discord.ext import commands
import json
import re
import io
from pathlib import Path
import openpyxl
from utils import carregar_dados, salvar_dados


class LevelUp(commands.Cog):
    TEMP_MARKER = "`LVL_UP_TEMP`"
    BACKUP_MARKER = "SISTEMA_BACKUP_ID"

    def __init__(self, bot):
        self.bot = bot
        self.database_path = Path(__file__).resolve().parent.parent / "database.json"
        self.planilha_path = Path(__file__).resolve().parent.parent / "planilha_geral.xlsx"

    # =========================
    # Utilidades
    # =========================
    def _normalizar(self, texto: str) -> str:
        if texto is None:
            return ""
        texto = str(texto).strip().lower()
        mapa = {
            "ç": "c", "ã": "a", "á": "a", "à": "a", "â": "a",
            "é": "e", "ê": "e", "í": "i", "ó": "o", "ô": "o", "õ": "o", "ú": "u"
        }
        for a, b in mapa.items():
            texto = texto.replace(a, b)
        return re.sub(r"\s+", " ", texto).strip()

    def _limpar_valor(self, texto: str) -> str:
        if not texto:
            return ""
        texto = str(texto).strip()
        texto = texto.replace("**", "").replace("*", "")
        texto = re.sub(r'^[\|\-\–—\s]+', '', texto)
        texto = re.sub(r'[\|\-\–—\s]+$', '', texto)
        return texto.strip()

    def _split_requisitos_virgula(self, texto: str) -> list[str]:
        if not texto:
            return []
        return [p.strip() for p in str(texto).split(",") if p.strip()]

    def _split_requisitos_com_ou(self, texto: str) -> list[str]:
        if not texto:
            return []
        partes = re.split(r'\bou\b', str(texto), flags=re.I)
        return [p.strip(" .;:") for p in partes if p.strip(" .;:")]

    def _eh_so_criacao_personagem(self, item: dict) -> bool:
        textos = [item.get("requisito", ""), item.get("especial", ""), item.get("descricao", "")]
        conteudo = self._normalizar(" ".join(str(t) for t in textos if t))
        padroes = [
            "so pode selecionar essa habilidade durante a criacao do personagem",
            "so pode selecionar esta habilidade durante a criacao do personagem",
            "apenas durante a criacao do personagem",
            "somente durante a criacao do personagem",
            "durante a criacao do personagem",
            "na criacao do personagem",
        ]
        return any(p in conteudo for p in padroes)

    def _eh_habilidade_acao(self, habilidade: dict) -> bool:
        tipo = self._normalizar(habilidade.get("tipo", ""))
        subtipo = self._normalizar(habilidade.get("subtipo", ""))
        return tipo in ("acao", "reacao") or subtipo in ("acao", "reacao")

    # =========================
    # Leitura do tópico atual
    # =========================
    async def _ler_textos_do_topico(self, canal: discord.Thread) -> list[str]:
        textos = []
        async for msg in canal.history(limit=300, oldest_first=True):
            if msg.content:
                textos.append(msg.content)
            for att in msg.attachments:
                if att.filename.lower().endswith(".txt"):
                    try:
                        raw = await att.read()
                        for enc in ("utf-8", "latin-1"):
                            try:
                                textos.append(raw.decode(enc))
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass
        return textos

    def _extrair_campos_inline(self, texto: str) -> dict:
        dados = {}
        for linha in texto.splitlines():
            linha_limpa = linha.replace("**", "")
            for rotulo in [
                "Personagem", "Nome", "Raça", "Raca", "Classe",
                "Aprendizado", "Aprendizagem", "Aprendiz",
                "Caminho", "Nível", "Nivel", "Idiomas"
            ]:
                padrao = rf'{rotulo}\s*:\s*([^|\n]+)'
                m = re.search(padrao, linha_limpa, flags=re.I)
                if m:
                    dados[rotulo.lower()] = self._limpar_valor(m.group(1))
        return dados

    def _extrair_habilidades_da_ficha(self, texto: str) -> list[str]:
        habilidades = []

        blocos = re.findall(
            r'(?:\*\*HABILIDADES:\*\*|HABILIDADES:)\s*(.*?)(?:\n\s*\n|\n\*\*|\Z)',
            texto,
            flags=re.I | re.S
        )
        for bloco in blocos:
            for item in re.split(r',|;|\n', bloco):
                item = re.sub(r'^[\-\•\*\d\.\)\s]+', '', item).strip()
                if item:
                    habilidades.append(item)

        for secao in ("Habilidades de Ação", "Habilidades de Suporte", "Habilidades"):
            blocos = re.findall(rf'\[{secao}\]\s*(.*?)(?:\n\s*\[|\Z)', texto, flags=re.I | re.S)
            for bloco in blocos:
                for linha in bloco.splitlines():
                    linha = linha.strip()
                    if not linha:
                        continue
                    nome = re.sub(r'\s*\[.*$', '', linha).strip()
                    if nome:
                        habilidades.append(nome)

        vistos = set()
        final = []
        for hab in habilidades:
            chave = self._nome_base_habilidade(hab)
            if chave and chave not in vistos:
                vistos.add(chave)
                final.append(self._limpar_valor(hab))
        return final

    def _extrair_idiomas_da_ficha(self, texto: str, inline: dict) -> list[str]:
        idiomas = []
        valor_inline = inline.get("idiomas", "")
        if valor_inline:
            idiomas.extend([self._limpar_valor(x) for x in re.split(r',|;|/|\n', valor_inline) if self._limpar_valor(x)])
        m = re.search(r'Idiomas\s+(.+?)(?:\n|$)', texto, flags=re.I)
        if m:
            idiomas.extend([self._limpar_valor(x) for x in re.split(r',|;|/|\n', m.group(1)) if self._limpar_valor(x)])

        vistos = set()
        final = []
        for idioma in idiomas:
            chave = self._normalizar(idioma)
            if chave and chave not in vistos:
                vistos.add(chave)
                final.append(idioma)
        return final

    def _extrair_raca_classe_livre(self, texto: str) -> dict:
        linhas = [l.strip() for l in texto.splitlines() if l.strip()]
        bloco = " ".join(linhas[:8])
        raca = classe = aprendizado = caminho = ""

        m = re.search(r'([A-Za-zÀ-ÿ]+)\s*[♀♂]?\s+([A-Za-zÀ-ÿ]+)', bloco)
        if m:
            raca = self._limpar_valor(m.group(1))
            classe = self._limpar_valor(m.group(2))

        m_ap = re.search(r'aprendiz(?:ado|agem)?\s+de\s+([A-Za-zÀ-ÿ]+)', bloco, flags=re.I)
        if m_ap:
            aprendizado = self._limpar_valor(m_ap.group(1))

        m_cam = re.search(r'(?:no\s+)?caminho\s+de\s+([A-Za-zÀ-ÿ]+)', bloco, flags=re.I)
        if m_cam:
            caminho = self._limpar_valor(m_cam.group(1))

        return {"raca": raca, "classe": classe, "aprendizado": aprendizado, "caminho": caminho}

    def _extrair_dados_ficha(self, textos: list[str]) -> dict:
        combinado = "\n".join(textos)
        inline = self._extrair_campos_inline(combinado)

        nome = inline.get("personagem") or inline.get("nome", "")
        if not nome:
            m = re.search(r'FICHA DE PERSONAGEM:\s*(.+)', combinado, flags=re.I)
            if m:
                nome = self._limpar_valor(m.group(1))
        if not nome:
            linhas = [l.strip() for l in combinado.splitlines() if l.strip()]
            if len(linhas) >= 2 and "," in linhas[1]:
                nome = self._limpar_valor(linhas[1].split(",", 1)[0])

        raca = inline.get("raça") or inline.get("raca", "")
        classe = inline.get("classe", "")
        caminho = inline.get("caminho", "")
        aprendizado = inline.get("aprendizado") or inline.get("aprendizagem") or inline.get("aprendiz", "")

        if not (raca and classe):
            livre = self._extrair_raca_classe_livre(combinado)
            raca = raca or livre["raca"]
            classe = classe or livre["classe"]
            aprendizado = aprendizado or livre["aprendizado"]
            caminho = caminho or livre["caminho"]

        classes = []
        if classe:
            classes.append(self._limpar_valor(classe))
        if aprendizado:
            classes.append(self._limpar_valor(aprendizado))

        classes_final = []
        vistos = set()
        for cls in classes:
            chave = self._normalizar(cls)
            if chave and chave not in vistos:
                vistos.add(chave)
                classes_final.append(cls)

        nivel = 0
        for fonte in [inline.get("nível"), inline.get("nivel")]:
            if fonte:
                m = re.search(r'\d+', str(fonte))
                if m:
                    nivel = int(m.group())
                    break
        if nivel == 0:
            for pad in [r'(?:N[ií]vel|Level)\s*[:\-]?\s*(\d+)', r'\[Geral\].*?N[ií]vel\s+(\d+)']:
                m = re.search(pad, combinado, flags=re.I | re.S)
                if m:
                    nivel = int(m.group(1))
                    break

        atributos = {"forca": 0, "agilidade": 0, "inteligencia": 0, "vontade": 0}
        pads = {
            "forca": [r'\bFOR\b\s*(\d+)', r'Força\s*[:\-]?\s*(\d+)', r'Forca\s*[:\-]?\s*(\d+)'],
            "agilidade": [r'\bAGI\b\s*(\d+)', r'Agilidade\s*[:\-]?\s*(\d+)'],
            "inteligencia": [r'\bINT\b\s*(\d+)', r'Intelig[eê]ncia\s*[:\-]?\s*(\d+)', r'Inteligencia\s*[:\-]?\s*(\d+)'],
            "vontade": [r'\bVON\b\s*(\d+)', r'Vontade\s*[:\-]?\s*(\d+)'],
        }
        for chave, lista in pads.items():
            for pad in lista:
                m = re.search(pad, combinado, flags=re.I)
                if m:
                    atributos[chave] = int(m.group(1))
                    break

        return {
            "nome": self._limpar_valor(nome),
            "raca": self._limpar_valor(raca),
            "classes": classes_final,
            "caminho": self._limpar_valor(caminho),
            "nivel": nivel,
            "atributos": atributos,
            "habilidades": self._extrair_habilidades_da_ficha(combinado),
            "idiomas": self._extrair_idiomas_da_ficha(combinado, inline),
        }

    # =========================
    # Banco / Requisitos
    # =========================
    def _carregar_database(self) -> dict:
        with open(self.database_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _todas_habilidades_de_entrada(self, entrada: dict) -> list[dict]:
        habilidades = []
        auto = entrada.get("Habilidade Automática")
        if isinstance(auto, dict) and auto.get("nome"):
            habilidades.append(auto)
        for chave in ("Habilidades Básicas", "Habilidades Avançadas", "Habilidade Final", "Habilidades Finais", "Habilidades Extras"):
            lista = entrada.get(chave, [])
            if isinstance(lista, list):
                habilidades.extend([h for h in lista if isinstance(h, dict) and h.get("nome")])
        return habilidades

    def _nome_base_habilidade(self, nome: str) -> str:
        nome_limpo = self._limpar_valor(nome)
        nome_norm = self._normalizar(nome_limpo)

        especiais = ("espirito animal", "pacto", "dogma")

        for base in especiais:
            if nome_norm == base:
                return base
            if nome_norm.startswith(base + ":"):
                return base

        return nome_norm

    def _tem_habilidade(self, player: dict, nome_hab: str) -> bool:
        alvo = self._nome_base_habilidade(nome_hab)
        return any(
            self._nome_base_habilidade(h) == alvo
            for h in player.get("habilidades", [])
        )

    def _tem_idioma(self, player: dict, idioma: str) -> bool:
        alvo = self._normalizar(idioma)
        return any(self._normalizar(i) == alvo for i in player.get("idiomas", []))

    def _checar_requisito_simples(self, req: str, player: dict) -> bool:
        req_original = req.strip()
        req_norm = self._normalizar(req_original)
        if not req_norm:
            return True

        if any(chave in req_norm for chave in [
            "ter abandonado um voto", "abandonado um voto",
            "depende do mestre", "a criterio do mestre", "à criterio do mestre", "criterio do mestre"
        ]):
            return True

        m = re.match(r'^nivel\s+(\d+)$', req_norm)
        if m:
            return player.get("nivel", 0) >= int(m.group(1))

        for nome_attr, chave in {"forca":"forca","agilidade":"agilidade","inteligencia":"inteligencia","vontade":"vontade"}.items():
            m = re.match(rf'^{nome_attr}\s+(\d+)$', req_norm)
            if m:
                return player["atributos"].get(chave, 0) >= int(m.group(1))

        m_idioma = re.match(r'^(?:idioma|idiomas?)\s+(.+)$', req_norm)
        if m_idioma:
            return self._tem_idioma(player, m_idioma.group(1).strip())

        if self._tem_idioma(player, req_original):
            return True

        if req_norm.startswith("aprendiz de "):
            return True

        if self._normalizar(player.get("raca", "")) == req_norm:
            return True

        for cls in player.get("classes", []):
            if self._normalizar(cls) == req_norm:
                return True

        if self._normalizar(player.get("caminho", "")) == req_norm:
            return True

        if self._tem_habilidade(player, req_original):
            return True

        return False

    def _atende_todos_requisitos(self, requisito_texto: str, player: dict) -> bool:
        requisitos = self._split_requisitos_virgula(requisito_texto)
        if not requisitos:
            return True
        for req in requisitos:
            alternativas = self._split_requisitos_com_ou(req)
            if alternativas:
                if not any(self._checar_requisito_simples(alt, player) for alt in alternativas):
                    return False
            else:
                if not self._checar_requisito_simples(req, player):
                    return False
        return True

    # =========================
    # Coleta / Seleção
    # =========================
    def _coletar_por_nome(self, db: dict, secao: str, nomes: list[str]) -> list[dict]:
        saida = []
        nomes_norm = {self._normalizar(n) for n in nomes if n}
        for pasta, pasta_data in db.get("habilidades", {}).items():
            for entrada in pasta_data.get(secao, []):
                if self._normalizar(entrada.get("Nome", "")) in nomes_norm:
                    for hab in self._todas_habilidades_de_entrada(entrada):
                        if self._eh_so_criacao_personagem(hab):
                            continue
                        saida.append({
                            "categoria": {"Raças": "Raça", "Classes": "Classe", "Caminhos": "Caminho"}[secao],
                            "nome": hab.get("nome", ""),
                            "tipo": hab.get("tipo", ""),
                            "subtipo": hab.get("subtipo", ""),
                            "requisito": hab.get("requisito", ""),
                            "especial": hab.get("especial", ""),
                            "descricao": hab.get("descricao", ""),
                            "origem": entrada.get("Nome", ""),
                            "pasta": pasta,
                        })
        return saida

    def _coletar_gerais(self, db: dict) -> list[dict]:
        saida = []
        for pasta, pasta_data in db.get("habilidades", {}).items():
            for hab in pasta_data.get("Habilidades Gerais", []):
                if isinstance(hab, dict) and hab.get("nome"):
                    if self._eh_so_criacao_personagem(hab):
                        continue
                    saida.append({
                        "categoria": "Gerais",
                        "nome": hab.get("nome", ""),
                        "tipo": hab.get("tipo", ""),
                        "subtipo": hab.get("subtipo", ""),
                        "requisito": hab.get("requisito", ""),
                        "especial": hab.get("especial", ""),
                        "descricao": hab.get("descricao", ""),
                        "origem": "Habilidades Gerais",
                        "pasta": pasta,
                    })
        return saida

    def _coletar_caminhos_disponiveis(self, db: dict, player: dict) -> list[dict]:
        saida = []
        for pasta, pasta_data in db.get("habilidades", {}).items():
            for entrada in pasta_data.get("Caminhos", []):
                if not isinstance(entrada, dict):
                    continue
                nome_caminho = entrada.get("Nome", "").strip()
                if not nome_caminho:
                    continue

                reqs = []
                requisitos_dict = entrada.get("Requisitos", {})
                if isinstance(requisitos_dict, dict):
                    for chave, valor in requisitos_dict.items():
                        chave = str(chave).strip()
                        valor = str(valor).strip()
                        chave_norm = self._normalizar(chave)
                        if "para seguir este caminho" in chave_norm or "deve preencher" in chave_norm or "precisa preencher" in chave_norm:
                            continue
                        reqs.append(f"{chave} {valor}".strip() if valor else chave)

                if all(self._checar_requisito_simples(req, player) for req in reqs):
                    saida.append({"nome": nome_caminho, "requisitos": ", ".join(reqs) if reqs else "Sem requisitos", "pasta": pasta})

        vistos = set()
        final = []
        for item in saida:
            chave = self._normalizar(item["nome"])
            if chave not in vistos:
                vistos.add(chave)
                final.append(item)
        return final

    def _montar_opcoes(self, db: dict, player: dict):
        candidatos = []
        if player.get("raca"):
            candidatos.extend(self._coletar_por_name(db, "Raças", [player["raca"]]) if False else self._coletar_por_nome(db, "Raças", [player["raca"]]))
        if player.get("classes"):
            candidatos.extend(self._coletar_por_nome(db, "Classes", player["classes"]))
        candidatos.extend(self._coletar_gerais(db))

        caminho_atual = player.get("caminho", "").strip()
        caminhos_disponiveis = []
        if caminho_atual:
            candidatos.extend(self._coletar_por_nome(db, "Caminhos", [caminho_atual]))
        else:
            caminhos_disponiveis = self._coletar_caminhos_disponiveis(db, player)

        resultados = {"Raça": [], "Classe": [], "Gerais": [], "Caminho": []}
        for item in candidatos:
            nome_hab = item.get("nome", "").strip()
            if not nome_hab:
                continue
            if self._tem_habilidade(player, nome_hab):
                continue
            if not self._atende_todos_requisitos(item.get("requisito", ""), player):
                continue
            resultados[item["categoria"]].append(item)

        for categoria in resultados:
            vistos = set()
            filtrada = []
            for item in resultados[categoria]:
                chave = self._normalizar(item["nome"])
                if chave not in vistos:
                    vistos.add(chave)
                    filtrada.append(item)
            resultados[categoria] = filtrada

        return resultados, caminhos_disponiveis

    def _achar_habilidade_por_nome(self, resultados: dict, nome_habilidade: str):
        alvo = self._normalizar(nome_habilidade)
        for categoria in ("Raça", "Classe", "Gerais", "Caminho"):
            for item in resultados.get(categoria, []):
                if self._normalizar(item["nome"]) == alvo:
                    return item
        return None

    async def _achar_thread_do_usuario(self, guild: discord.Guild, usuario: discord.Member):
        dados = carregar_dados()
        gid = str(guild.id)
        info = dados.get("personagens", {}).get(gid, {}).get(str(usuario.id))
        if info and info.get("thread_id"):
            try:
                return await guild.fetch_channel(int(info["thread_id"]))
            except Exception:
                return None
        return None

    # =========================
    # Atualização de mensagens / backup
    # =========================
    async def _limpar_msgs_temp(self, thread: discord.Thread):
        async for msg in thread.history(limit=50):
            if msg.author.id == self.bot.user.id and self.TEMP_MARKER in (msg.content or ""):
                try:
                    await msg.delete()
                except Exception:
                    pass

    async def _mandar_temp(self, ctx, texto: str):
        if ctx.interaction:
            await ctx.interaction.followup.send(texto, ephemeral=True)
        else:
            await ctx.send(f"{self.TEMP_MARKER}\n{texto}", delete_after=120)

    async def _achar_msg_ficha(self, thread: discord.Thread):
        async for msg in thread.history(limit=50, oldest_first=True):
            if "🛡️ **FICHA DE PERSONAGEM:" in (msg.content or ""):
                return msg
        return None

    async def _achar_backup(self, thread: discord.Thread):
        async for msg in thread.history(limit=50):
            if msg.author.id == self.bot.user.id and self.BACKUP_MARKER in (msg.content or ""):
                return msg
        return None

    def _inserir_habilidade_na_msg_ficha(self, content: str, nome_habilidade: str) -> str:
        padrao = r'(\*\*HABILIDADES:\*\*\n)(.*?)(\n\n\*\*INVENTÁRIO:\*\*)'
        m = re.search(padrao, content, flags=re.S)
        if not m:
            return content
        atual = m.group(2).strip()
        lista = [x.strip() for x in atual.split(",") if x.strip()] if atual else []
        if self._normalizar(nome_habilidade) not in [self._normalizar(x) for x in lista]:
            lista.append(nome_habilidade)
        novo = ", ".join(lista)
        return content[:m.start()] + m.group(1) + novo + m.group(3) + content[m.end():]

    def _inserir_item_na_msg_ficha(self, content: str, nome_item: str, quantidade: int) -> str:
        padrao = r'(\*\*INVENTÁRIO:\*\*\n)(.*)$'
        m = re.search(padrao, content, flags=re.S)
        linha_item = f"{nome_item} (x{quantidade})"
        if not m:
            return content + f"\n\n**INVENTÁRIO:**\n{linha_item}"
        atual = m.group(2).strip()
        linhas = [l.strip() for l in atual.splitlines() if l.strip()]
        linhas.append(linha_item)
        novo = "\n".join(linhas)
        return content[:m.start()] + m.group(1) + novo

    def _inserir_em_secao_txt(self, raw_txt: str, secao: str, linha_base: str) -> str:
        padrao_secao = rf'(\[{re.escape(secao)}\]\n)(.*?)(?=\n(?:\[[^\]]+\]|Equipamentos)|\Z)'
        m = re.search(padrao_secao, raw_txt, flags=re.S | re.I)
        if m:
            bloco = m.group(2).rstrip()
            if self._normalizar(linha_base) in self._normalizar(bloco):
                return raw_txt
            novo_bloco = bloco + ("\n" if bloco else "") + linha_base + "\n"
            return raw_txt[:m.start(2)] + novo_bloco + raw_txt[m.end(2):]

        marcador_equip = re.search(r'\n(?:Equipamentos|\[Equipamentos\])', raw_txt, flags=re.I)
        insert_text = f"\n[{secao}]\n{linha_base}\n"
        if marcador_equip:
            pos = marcador_equip.start()
            return raw_txt[:pos] + insert_text + raw_txt[pos:]
        return raw_txt.rstrip() + insert_text

    def _inserir_habilidade_no_txt(self, raw_txt: str, habilidade: dict) -> str:
        nome = habilidade["nome"]
        descricao = habilidade.get("descricao", "")
        marcador_auto = " [Escolhida no Level Up]"
        linha_base = f"{nome}{marcador_auto}"
        if descricao:
            linha_base += f" ({descricao})"

        if self._normalizar(nome) in self._normalizar(raw_txt):
            return raw_txt

        secao_preferida = "Habilidades de Ação" if self._eh_habilidade_acao(habilidade) else "Habilidades de Suporte"
        return self._inserir_em_secao_txt(raw_txt, secao_preferida, linha_base)

    def _formatar_linha_item_txt(self, nome_item: str, quantidade: int, peso: float, custo: float) -> str:
        peso_txt = str(int(peso)) if float(peso).is_integer() else str(peso)
        custo_txt = str(int(custo)) if float(custo).is_integer() else str(custo)
        return f".{nome_item:<38} x{quantidade:<4} {peso_txt:<8} {custo_txt}"

    def _inserir_item_no_txt(self, raw_txt: str, nome_item: str, quantidade: int, peso: float, custo: float) -> str:
        linha_item = self._formatar_linha_item_txt(nome_item, quantidade, peso, custo)

        padrao_equip = r'(Equipamentos[^\n]*\n)(.*?)(?=\n\n|----------|\Z)'
        m = re.search(padrao_equip, raw_txt, flags=re.S | re.I)
        if m:
            bloco = m.group(2).rstrip()
            novo_bloco = bloco + ("\n" if bloco else "") + linha_item + "\n"
            return raw_txt[:m.start(2)] + novo_bloco + raw_txt[m.end(2):]

        return raw_txt.rstrip() + f"\n\nEquipamentos                            qtd.  peso   custo\n{linha_item}\n"

    async def _aplicar_habilidade(self, thread: discord.Thread, player_name: str, habilidade: dict):
        msg_ficha = await self._achar_msg_ficha(thread)
        if msg_ficha:
            novo = self._inserir_habilidade_na_msg_ficha(msg_ficha.content, habilidade["nome"])
            if novo != msg_ficha.content:
                await msg_ficha.edit(content=novo)

        backup = await self._achar_backup(thread)
        if backup:
            raw_txt = ""
            for att in backup.attachments:
                if att.filename.lower().endswith(".txt"):
                    content = await att.read()
                    try:
                        raw_txt = content.decode("utf-8")
                    except Exception:
                        raw_txt = content.decode("latin-1")
                    break

            if raw_txt:
                novo_txt = self._inserir_habilidade_no_txt(raw_txt, habilidade)
                await backup.delete()
                await thread.send(
                    content=f"💾 **BACKUP ATUALIZADO:**\n`{self.BACKUP_MARKER}`",
                    files=[discord.File(io.BytesIO(novo_txt.encode("utf-8")), filename=f"{player_name}.txt")]
                )

        dados = carregar_dados()
        guild_id = str(thread.guild.id)
        for uid, info in dados.get("personagens", {}).get(guild_id, {}).items():
            if str(info.get("thread_id")) == str(thread.id):
                habilidades = info.get("habilidades", [])
                if not isinstance(habilidades, list):
                    habilidades = []
                if self._normalizar(habilidade["nome"]) not in [self._normalizar(x) for x in habilidades]:
                    habilidades.append(habilidade["nome"])
                    info["habilidades"] = habilidades
                    salvar_dados(dados)
                break

    async def _aplicar_item(self, thread: discord.Thread, player_name: str, nome_item: str, quantidade: int, peso: float, custo: float):
        msg_ficha = await self._achar_msg_ficha(thread)
        if msg_ficha:
            novo = self._inserir_item_na_msg_ficha(msg_ficha.content, nome_item, quantidade)
            if novo != msg_ficha.content:
                await msg_ficha.edit(content=novo)

        backup = await self._achar_backup(thread)
        if backup:
            raw_txt = ""
            for att in backup.attachments:
                if att.filename.lower().endswith(".txt"):
                    content = await att.read()
                    try:
                        raw_txt = content.decode("utf-8")
                    except Exception:
                        raw_txt = content.decode("latin-1")
                    break

            if raw_txt:
                novo_txt = self._inserir_item_no_txt(raw_txt, nome_item, quantidade, peso, custo)
                await backup.delete()
                await thread.send(
                    content=f"💾 **BACKUP ATUALIZADO:**\n`{self.BACKUP_MARKER}`",
                    files=[discord.File(io.BytesIO(novo_txt.encode("utf-8")), filename=f"{player_name}.txt")]
                )

    # =========================
    # Fluxos
    # =========================
    async def _mostrar_opcoes(self, ctx, thread: discord.Thread):
        textos = await self._ler_textos_do_topico(thread)
        if not textos:
            return await self._mandar_temp(ctx, "❌ Não encontrei textos ou anexos .txt neste tópico.")

        player = self._extrair_dados_ficha(textos)
        db = self._carregar_database()
        resultados, caminhos_disponiveis = self._montar_opcoes(db, player)

        classes_txt = ", ".join(player.get("classes", [])) if player.get("classes") else "Não identificado"
        idiomas_txt = ", ".join(player.get("idiomas", [])) if player.get("idiomas") else "Nenhum"

        linhas = []
        linhas.append(self.TEMP_MARKER)
        linhas.append("🔍 **Analisando personagem do tópico atual...**")
        linhas.append(f"👤 Personagem: **{player.get('nome') or 'Não identificado'}**")
        linhas.append(f"🧬 Raça: **{player.get('raca') or 'Não identificado'}**")
        linhas.append(f"⚔️ Classes analisadas: **{classes_txt}**")
        linhas.append(f"🛤️ Caminho: **{player.get('caminho') or 'Nenhum'}**")
        linhas.append(f"📊 Nível: **{player.get('nivel', 0)}**")
        linhas.append(f"🗣️ Idiomas: **{idiomas_txt}**")
        linhas.append(f"🧠 Habilidades encontradas na ficha: **{len(player.get('habilidades', []))}**")
        linhas.append("\n✨ **Opções disponíveis para o level up:**")

        total = 0
        for categoria in ("Raça", "Classe", "Gerais", "Caminho"):
            itens = resultados[categoria]
            if not itens:
                continue
            total += len(itens)
            linhas.append(f"\n**{categoria} ({len(itens)}):**")
            for item in itens[:80]:
                tipo_txt = ""
                if item["tipo"] and item["subtipo"]:
                    tipo_txt = f" ({item['tipo']} - {item['subtipo']})"
                elif item["tipo"]:
                    tipo_txt = f" ({item['tipo']})"
                elif item["subtipo"]:
                    tipo_txt = f" ({item['subtipo']})"
                linhas.append(f"• **{item['nome']}**{tipo_txt} — origem: {item['origem']}")

        if not player.get("caminho"):
            linhas.append("\n**Caminhos disponíveis para entrar:**")
            if caminhos_disponiveis:
                for cam in caminhos_disponiveis[:40]:
                    linhas.append(f"• **{cam['nome']}** — requisitos: {cam['requisitos']}")
            else:
                linhas.append("• Nenhum caminho disponível com base na ficha atual.")

        if total == 0 and player.get("caminho"):
            linhas.append("\n❌ Nenhuma habilidade elegível foi encontrada com base na ficha atual.")
        else:
            linhas.append("\nPara escolher, use: **/lvl_up @personagem nome_habilidade**")

        resposta = "\n".join(linhas)
        if ctx.interaction:
            await ctx.interaction.followup.send(resposta, ephemeral=True)
        else:
            await self._limpar_msgs_temp(thread)
            partes = []
            atual = ""
            for linha in resposta.splitlines(keepends=True):
                if len(atual) + len(linha) > 1800:
                    partes.append(atual)
                    atual = linha
                else:
                    atual += linha
            if atual:
                partes.append(atual)
            for parte in partes:
                await thread.send(parte, delete_after=120)

    async def _escolher_habilidade(self, ctx, thread: discord.Thread, nome_habilidade: str, bypass_requisitos: bool = False):
        textos = await self._ler_textos_do_topico(thread)
        if not textos:
            return await self._mandar_temp(ctx, "❌ Não encontrei textos ou anexos .txt neste tópico.")

        player = self._extrair_dados_ficha(textos)
        db = self._carregar_database()
        resultados, _ = self._montar_opcoes(db, player)

        escolhida = self._achar_habilidade_por_nome(resultados, nome_habilidade)

        if not escolhida and bypass_requisitos:
            candidatos = []
            if player.get("raca"):
                candidatos.extend(self._coletar_por_nome(db, "Raças", [player["raca"]]))
            if player.get("classes"):
                candidatos.extend(self._coletar_por_nome(db, "Classes", player["classes"]))
            candidatos.extend(self._coletar_gerais(db))
            if player.get("caminho"):
                candidatos.extend(self._coletar_por_nome(db, "Caminhos", [player["caminho"]]))
            for item in candidatos:
                if self._normalizar(item["nome"]) == self._normalizar(nome_habilidade):
                    escolhida = item
                    break

        if not escolhida:
            return await self._mandar_temp(ctx, f"❌ Habilidade **{nome_habilidade}** não está disponível para este personagem agora.")

        await self._aplicar_habilidade(thread, player.get("nome") or "personagem", escolhida)
        await self._limpar_msgs_temp(thread)

        detalhe = (
            f"✅ **Habilidade adicionada:** **{escolhida['nome']}**\n"
            f"📁 Origem: {escolhida['origem']} | Pasta: {escolhida['pasta']}\n"
            f"📌 Requisitos: {escolhida['requisito'] or 'Sem requisitos'}\n"
            f"📝 A ficha do tópico e o backup `.txt` foram atualizados."
        )
        return await self._mandar_temp(ctx, detalhe)


    def _buscar_item_planilha(self, nome_item: str):
        if not self.planilha_path.exists():
            return None

        termo_norm = self._normalizar(nome_item)

        try:
            wb = openpyxl.load_workbook(self.planilha_path, data_only=True)
        except Exception:
            return None

        melhor_parcial = None

        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue

            headers = ["" if c is None else str(c).strip() for c in rows[0]]

            nome_idx = None
            peso_idx = None
            custo_idx = None

            for i, h in enumerate(headers):
                h_norm = self._normalizar(h)
                if nome_idx is None and h_norm in {"nome", "item", "itens", "nome do item"}:
                    nome_idx = i
                if peso_idx is None and h_norm == "peso":
                    peso_idx = i
                if custo_idx is None and h_norm in {"custo", "valor", "preco", "preço"}:
                    custo_idx = i

            if nome_idx is None:
                continue

            for row in rows[1:]:
                if nome_idx >= len(row):
                    continue

                nome_val = "" if row[nome_idx] is None else str(row[nome_idx]).strip()
                if not nome_val:
                    continue

                nome_val_norm = self._normalizar(nome_val)

                peso_val = row[peso_idx] if peso_idx is not None and peso_idx < len(row) else 0
                custo_val = row[custo_idx] if custo_idx is not None and custo_idx < len(row) else 0

                try:
                    peso_num = float(peso_val) if peso_val not in (None, "") else 0.0
                except Exception:
                    peso_num = 0.0

                try:
                    custo_num = float(custo_val) if custo_val not in (None, "") else 0.0
                except Exception:
                    custo_num = 0.0

                item = {
                    "nome": nome_val,
                    "peso": peso_num,
                    "custo": custo_num,
                    "aba": ws.title,
                }

                if nome_val_norm == termo_norm:
                    return item

                if termo_norm in nome_val_norm and melhor_parcial is None:
                    melhor_parcial = item

        return melhor_parcial

    # =========================
    # Comandos
    # =========================
    @commands.hybrid_command(name="lvl_up", description="Lista ou escolhe habilidades disponíveis para o personagem do tópico atual.")
    async def lvl_up(self, ctx, personagem: discord.Member | None = None, *, nome_habilidade: str | None = None):
        try:
            if ctx.interaction:
                await ctx.interaction.response.defer(ephemeral=True)

            if not isinstance(ctx.channel, discord.Thread):
                return await self._mandar_temp(ctx, "❌ Use este comando dentro do tópico da ficha do personagem.")

            if nome_habilidade:
                return await self._escolher_habilidade(ctx, ctx.channel, nome_habilidade, bypass_requisitos=False)

            return await self._mostrar_opcoes(ctx, ctx.channel)

        except Exception as e:
            await self._mandar_temp(ctx, f"⚠️ Erro no /lvl_up: `{type(e).__name__}: {e}`")
            print(f"[ERRO lvl_up] {type(e).__name__}: {e}")

    @commands.hybrid_command(name="mestre_hab", description="Mestre adiciona uma habilidade a um personagem.")
    async def mestre_hab(self, ctx, usuario: discord.Member, *, nome_habilidade: str):
        try:
            if ctx.interaction:
                await ctx.interaction.response.defer(ephemeral=True)

            dados = carregar_dados()
            gid = str(ctx.guild.id)
            role_id = dados.get("config", {}).get(gid, {}).get("mestre_role")
            eh_admin = ctx.author.guild_permissions.administrator
            eh_role = role_id and any(r.id == int(role_id) for r in ctx.author.roles)
            if not (eh_admin or eh_role):
                return await self._mandar_temp(ctx, "❌ Apenas o mestre ou administrador pode usar este comando.")

            thread = await self._achar_thread_do_usuario(ctx.guild, usuario)
            if not thread:
                return await self._mandar_temp(ctx, f"❌ Não encontrei a thread da ficha de **{usuario.display_name}**.")

            return await self._escolher_habilidade(ctx, thread, nome_habilidade, bypass_requisitos=True)

        except Exception as e:
            await self._mandar_temp(ctx, f"⚠️ Erro no /mestre_hab: `{type(e).__name__}: {e}`")
            print(f"[ERRO mestre_hab] {type(e).__name__}: {e}")

    @commands.hybrid_command(name="mestre_item", description="Mestre adiciona um item a um personagem usando peso e custo da planilha.")
    async def mestre_item(self, ctx, usuario: discord.Member, nome_item: str, quantidade: int):
        try:
            if ctx.interaction:
                await ctx.interaction.response.defer(ephemeral=True)

            dados = carregar_dados()
            gid = str(ctx.guild.id)
            role_id = dados.get("config", {}).get(gid, {}).get("mestre_role")
            eh_admin = ctx.author.guild_permissions.administrator
            eh_role = role_id and any(r.id == int(role_id) for r in ctx.author.roles)
            if not (eh_admin or eh_role):
                return await self._mandar_temp(ctx, "❌ Apenas o mestre ou administrador pode usar este comando.")

            thread = await self._achar_thread_do_usuario(ctx.guild, usuario)
            if not thread:
                return await self._mandar_temp(ctx, f"❌ Não encontrei a thread da ficha de **{usuario.display_name}**.")

            item_planilha = self._buscar_item_planilha(nome_item)
            if not item_planilha:
                return await self._mandar_temp(ctx, f"❌ Não encontrei **{nome_item}** na planilha_geral.xlsx.")

            textos = await self._ler_textos_do_topico(thread)
            if not textos:
                return await self._mandar_temp(ctx, "❌ Não encontrei textos ou anexos .txt neste tópico.")

            player = self._extrair_dados_ficha(textos)
            await self._aplicar_item(
                thread,
                player.get("nome") or "personagem",
                item_planilha["nome"],
                quantidade,
                item_planilha["peso"],
                item_planilha["custo"]
            )
            await self._limpar_msgs_temp(thread)

            return await self._mandar_temp(
                ctx,
                f"✅ **Item adicionado:** **{item_planilha['nome']}**"
                f"📦 Quantidade: {quantidade}"
                f"⚖️ Peso (planilha): {item_planilha['peso']}"
                f"💰 Custo (planilha): {item_planilha['custo']}"
                f"📄 Aba: {item_planilha['aba']}"
                f"📝 A ficha do tópico e o backup `.txt` foram atualizados."
            )

        except Exception as e:
            await self._mandar_temp(ctx, f"⚠️ Erro no /mestre_item: `{type(e).__name__}: {e}`")
            print(f"[ERRO mestre_item] {type(e).__name__}: {e}")


async def setup(bot):
    await bot.add_cog(LevelUp(bot))
