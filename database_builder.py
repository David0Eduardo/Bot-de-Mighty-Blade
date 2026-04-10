import json
import os
import re
import unicodedata
from pathlib import Path

class DatabaseBuilder:
    def __init__(self, arquivos_dir="arquivos"):
        self.arquivos_dir = arquivos_dir
        self.database = {
            "habilidades": {},
            "metadata": {
                "total_pastas": 0,
                "total_arquivos": 0,
                "pastas": []
            }
        }


    def _classificar_arquivo(self, nome_arquivo):
        """Classifica o arquivo como habilidade, equipamento ou texto genérico."""
        nome_lower = nome_arquivo.lower()

        # Classificação de habilidades apenas para arquivos de habilidade válidos.
        if nome_lower in [
            'hab_geral.txt',
            'hab_racas.txt',
            'hab_classes.txt',
            'hab_caminhos.txt'
        ]:
            return "habilidades"

        # Classificação de equipamentos
        if "equip" in nome_lower:
            return "equipamentos"

        # Tudo mais é texto genérico
        return "textos"

    def _ler_arquivo(self, caminho):
        """Lê o conteúdo de um arquivo com tratamento de erro."""
        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            try:
                with open(caminho, 'r', encoding='latin-1') as f:
                    return f.read()
            except Exception as e:
                return f"[ERRO AO LER]: {str(e)}"
        except Exception as e:
            return f"[ERRO]: {str(e)}"

    def _remover_comentarios(self, conteudo):
        """Remove marcadores de comentário mantendo o texto interno."""
        conteudo = conteudo.replace('/*', '')
        conteudo = conteudo.replace('*/', '')
        conteudo = conteudo.replace('*\\', '')
        return conteudo

    def _limpar_linha(self, linha):
        """Normaliza uma linha para análise, removendo asteriscos e espaços extras."""
        return linha.lstrip('*').strip()

    def _normalizar_texto(self, texto):
        """Normaliza texto para comparação de headings e seções."""
        texto = texto.strip().lower()
        texto = unicodedata.normalize('NFKD', texto)
        texto = ''.join(ch for ch in texto if not unicodedata.combining(ch))
        return texto

    def _split_blocos(self, conteudo):
        """Divide o texto em blocos delimitados por linhas contendo apenas '---'."""
        blocos = re.split(r'(?m)^[ \t]*---[ \t]*$', conteudo)
        return [bloco.strip() for bloco in blocos if bloco.strip()]

    def _parse_lista(self, texto):
        texto = re.sub(r'[\.]+$', '', texto).strip()
        partes = re.split(r',|\be\b', texto)
        return [parte.strip() for parte in partes if parte.strip()]

    def _parse_requisitos(self, texto):
        requisitos = {}
        for linha in texto.splitlines():
            linha = self._limpar_linha(linha)
            if not linha:
                continue
            linha = re.sub(r'^[\*\-•]+\s*', '', linha)
            if ':' in linha:
                chave, valor = linha.split(':', 1)
            else:
                match = re.match(r'^(.+?)\s+([+-]?\d+)$', linha)
                if match:
                    chave, valor = match.group(1).strip(), match.group(2).strip()
                else:
                    chave, valor = linha, ''
            requisitos[chave.strip()] = valor.strip()
        return requisitos

    def _split_em_secoes(self, texto, alias_secoes):
        secoes = {}
        chave_atual = None
        linhas_atual = []

        for linha in texto.splitlines():
            linha_sem = self._normalizar_texto(linha)
            if linha_sem in alias_secoes:
                if chave_atual is not None:
                    secoes[chave_atual] = '\n'.join(linhas_atual).strip()
                chave_atual = alias_secoes[linha_sem]
                linhas_atual = []
                continue
            if chave_atual is not None:
                linhas_atual.append(linha)

        if chave_atual is not None:
            secoes[chave_atual] = '\n'.join(linhas_atual).strip()

        return secoes

    def _extrair_habilidades_por_secoes(self, texto, alias_secoes, fallback=None):
        secoes = self._split_em_secoes(texto, alias_secoes)
        if not secoes and fallback is not None:
            return {fallback: self._extrair_habilidades(texto)}
        return {nome: self._extrair_habilidades(conteudo) for nome, conteudo in secoes.items()}

    def _parse_metadados(self, texto):
        """Extrai metadados como atributos, bônus, classes comuns e requisitos de um bloco de texto."""
        meta = {
            "atributos": {},
            "bonus_atributo": {},
            "classes_comuns": [],
            "requisitos": {},
            "especial": "",
            "descricao": "",
            "habilidades": []
        }

        linhas = [self._limpar_linha(l) for l in texto.splitlines()]
        secao = None
        descricao = []
        especial = []
        requisitos = []

        for linha in linhas:
            if not linha:
                continue
            normalized = self._normalizar_texto(linha)

            if re.match(r'^bonus de atributo', normalized):
                secao = 'bonus'
                continue
            if re.match(r'^atributos iniciais', normalized):
                secao = 'atributos'
                continue
            if normalized.startswith('classes comuns'):
                secao = 'classes'
                if ':' in linha:
                    classes_text = linha.split(':', 1)[1].strip()
                    meta['classes_comuns'] = self._parse_lista(classes_text)
                    secao = None
                continue
            if normalized.startswith('habilidade automatica'):
                secao = 'auto'
                continue
            if normalized.startswith('requisitos') or normalized.startswith('requisito'):
                secao = 'requisitos'
                continue
            if normalized.startswith('especial'):
                secao = 'especial'
                linha = linha.split(':', 1)[1].strip() if ':' in linha else ''
                if linha:
                    especial.append(linha)
                continue
            if normalized.startswith('descricao') or normalized.startswith('descrição'):
                secao = 'descricao'
                linha = linha.split(':', 1)[1].strip() if ':' in linha else ''
                if linha:
                    descricao.append(linha)
                continue

            if secao == 'atributos':
                match = re.match(r'^(.+?)\s*[:\-]?\s*([+-]?\d+)$', linha)
                if match:
                    chave = match.group(1).strip()
                    valor = match.group(2).strip()
                    meta['atributos'][chave] = valor
                continue

            if secao == 'bonus':
                match = re.match(r'^(.+?)\s*[:\-]?\s*([+-]?\d+)$', linha)
                if match:
                    chave = match.group(1).strip()
                    valor = match.group(2).strip()
                    meta['bonus_atributo'][chave] = valor
                continue

            if secao == 'classes':
                if ':' in linha:
                    classes_text = linha.split(':', 1)[1].strip()
                    meta['classes_comuns'] = self._parse_lista(classes_text)
                else:
                    meta['classes_comuns'].extend(self._parse_lista(linha))
                continue

            if secao == 'requisitos':
                requisitos.append(linha)
                continue

            if secao == 'descricao':
                descricao.append(linha)
                continue

            if secao == 'especial':
                especial.append(linha)
                continue

        if requisitos:
            meta['requisitos'] = self._parse_requisitos('\n'.join(requisitos))
        if descricao:
            meta['descricao'] = ' '.join(descricao).strip()
        if especial:
            meta['especial'] = ' '.join(especial).strip()

        meta['habilidades'] = self._extrair_habilidades(texto)
        return meta

    def _extrair_entidades(self, conteudo):
        """Extrai entidades marcadas por **Nome** dentro de um arquivo."""
        pattern = re.compile(r'^\*\*(.+?)\*\*$', re.M)
        matches = list(pattern.finditer(conteudo))
        entidades = []

        for idx, match in enumerate(matches):
            nome = match.group(1).strip()
            inicio = match.end()
            fim = matches[idx + 1].start() if idx + 1 < len(matches) else len(conteudo)
            entidades.append((nome, conteudo[inicio:fim]))

        return entidades

    def _extrair_racas(self, conteudo):
        """Extrai definições de raças de arquivos de habilidades de raça."""
        racas = []
        for nome, corpo in self._extrair_entidades(conteudo):
            entrada = {
                "Nome": nome,
                "Atributos": {},
                "Classes Comuns": [],
                "Requisitos": {},
                "Habilidade Automática": {},
                "Habilidades Extras": []
            }

            comentario = re.search(r'/\*(.*?)\*\\', corpo, flags=re.S)
            if comentario:
                metadados = self._parse_metadados(comentario.group(1))
                entrada["Atributos"] = metadados["atributos"]
                entrada["Classes Comuns"] = metadados["classes_comuns"]
                entrada["Requisitos"] = metadados["requisitos"]
                if metadados["habilidades"]:
                    entrada["Habilidade Automática"] = metadados["habilidades"][0]
                corpo = corpo[:comentario.start()] + corpo[comentario.end():]

            secoes = self._extrair_habilidades_por_secoes(
                corpo,
                {
                    'habilidades extras': 'Habilidades Extras'
                },
                fallback='Habilidades Extras'
            )
            entrada["Habilidades Extras"] = secoes.get('Habilidades Extras', [])
            racas.append(entrada)

        return racas

    def _extrair_classes(self, conteudo):
        """Extrai definições de classes de arquivos de habilidades de classe."""
        classes = []
        for nome, corpo in self._extrair_entidades(conteudo):
            entrada = {
                "Nome": nome,
                "Bônus de Atributo": {},
                "Requisitos": {},
                "Habilidade Automática": {},
                "Habilidades Básicas": [],
                "Habilidades Avançadas": [],
                "Habilidade Final": [],
                "Habilidades Extras": []
            }

            comentario = re.search(r'/\*(.*?)\*\\', corpo, flags=re.S)
            if comentario:
                metadados = self._parse_metadados(comentario.group(1))
                entrada["Bônus de Atributo"] = metadados.get("bonus_atributo") or metadados.get("atributos")
                entrada["Requisitos"] = metadados.get("requisitos", {})
                if metadados["habilidades"]:
                    entrada["Habilidade Automática"] = metadados["habilidades"][0]
                corpo = corpo[:comentario.start()] + corpo[comentario.end():]

            secoes = self._extrair_habilidades_por_secoes(
                corpo,
                {
                    'habilidades basicas': 'Habilidades Básicas',
                    'habilidades avancadas': 'Habilidades Avançadas',
                    'habilidade final': 'Habilidade Final',
                    'habilidades finais': 'Habilidade Final',
                    'habilidades extras': 'Habilidades Extras'
                },
                fallback='Habilidades Extras'
            )
            entrada["Habilidades Básicas"] = secoes.get('Habilidades Básicas', [])
            entrada["Habilidades Avançadas"] = secoes.get('Habilidades Avançadas', [])
            entrada["Habilidade Final"] = secoes.get('Habilidade Final', [])
            entrada["Habilidades Extras"] = secoes.get('Habilidades Extras', [])
            classes.append(entrada)

        return classes

    def _extrair_caminhos(self, conteudo):
        """Extrai definições de caminhos de arquivos de habilidades de caminho."""
        caminhos = []
        for nome, corpo in self._extrair_entidades(conteudo):
            entrada = {
                "Nome": nome,
                "Bônus de Atributo": {},
                "Requisitos": {},
                "Habilidade Automática": {},
                "Habilidades Básicas": [],
                "Habilidades Avançadas": [],
                "Habilidade Final": [],
                "Habilidades Extras": []
            }

            comentario = re.search(r'/\*(.*?)\*\\', corpo, flags=re.S)
            if comentario:
                metadados = self._parse_metadados(comentario.group(1))
                entrada["Bônus de Atributo"] = metadados.get("bonus_atributo") or metadados.get("atributos")
                entrada["Requisitos"] = metadados.get("requisitos", {})
                if metadados["habilidades"]:
                    entrada["Habilidade Automática"] = metadados["habilidades"][0]
                corpo = corpo[:comentario.start()] + corpo[comentario.end():]

            secoes = self._extrair_habilidades_por_secoes(
                corpo,
                {
                    'habilidades basicas': 'Habilidades Básicas',
                    'habilidades avancadas': 'Habilidades Avançadas',
                    'habilidade final': 'Habilidade Final',
                    'habilidades finais': 'Habilidade Final',
                    'habilidades extras': 'Habilidades Extras'
                },
                fallback='Habilidades Extras'
            )
            entrada["Habilidades Básicas"] = secoes.get('Habilidades Básicas', [])
            entrada["Habilidades Avançadas"] = secoes.get('Habilidades Avançadas', [])
            entrada["Habilidade Final"] = secoes.get('Habilidade Final', [])
            entrada["Habilidades Extras"] = secoes.get('Habilidades Extras', [])
            caminhos.append(entrada)

        return caminhos

    def _extrair_habilidades(self, conteudo):
        """Extrai habilidades estruturadas do conteúdo do arquivo."""
        habilidades = []
        conteudo_limpo = self._remover_comentarios(conteudo)

        blocos = self._split_blocos(conteudo_limpo)
        for bloco in blocos:
            if not bloco:
                continue

            nome = None
            linhas = [self._limpar_linha(l) for l in bloco.splitlines() if l.strip()]
            for idx, linha in enumerate(linhas):
                match = re.match(r'^--\s*(.+?)\s*--\s*$', linha)
                if match:
                    nome = match.group(1).strip()
                    linhas = linhas[idx + 1:]
                    break

                if re.match(r'^habilidade\s+autom[aá]tica', linha, flags=re.I):
                    if idx + 1 < len(linhas) and not re.match(r'^habilidade', linhas[idx + 1], flags=re.I):
                        nome = linhas[idx + 1].strip()
                        nome = re.sub(r'^--\s*(.+?)\s*--$', r'\1', nome)
                        linhas = linhas[idx + 2:]
                        break

                if idx + 1 < len(linhas) and re.match(r'^Habilidade', linhas[idx + 1], flags=re.I) and not re.match(r'^habilidade\s+autom[aá]tica', linhas[idx + 1], flags=re.I):
                    nome = linha.strip()
                    nome = re.sub(r'^--\s*(.+?)\s*--$', r'\1', nome)
                    linhas = linhas[idx + 1:]
                    break

            if not nome or not linhas:
                continue

            if not any(re.match(r'^(Habilidade|M[uú]sica)', linha, flags=re.I) for linha in linhas[:3]):
                continue

            habilidade = {
                "nome": nome,
                "tipo": "",
                "subtipo": "",
                "requisito": "",
                "mana": "",
                "racas": "",
                "dificuldade": "",
                "especial": "",
                "descricao": "",
                "detalhes_adicionais": [],
            }

            primeira = linhas[0]
            tipo_match = re.search(r'Habilidade\s*\(([^)]+)\)', primeira, flags=re.I)
            if tipo_match:
                habilidade["tipo"] = tipo_match.group(1).strip()

            # Suporta também o formato especial usado em algumas Habilidades Finais de Bardo:
            # "Música - Tipo" (ex.: "Música - Canção")
            musica_match = re.match(r'^M[uú]sica\s*[-–—]\s*(.+?)\s*$', primeira, flags=re.I)
            if musica_match:
                habilidade["tipo"] = musica_match.group(1).strip()
                habilidade["subtipo"] = "Música"
            else:
                subtipo_match = re.search(r'[-–—]\s*(.+)$', primeira)
                if subtipo_match:
                    habilidade["subtipo"] = subtipo_match.group(1).strip()
                elif not habilidade["tipo"]:
                    tipo_unico = re.sub(r'^(?:Habilidade|M[uú]sica)\s*[-–—]\s*', '', primeira, flags=re.I).strip()
                    if tipo_unico:
                        habilidade["subtipo"] = tipo_unico

            i = 1
            campo_atual = None
            campos_multilinha = {
                'requisito': [],
                'descricao': [],
                'especial': []
            }

            while i < len(linhas):
                linha = linhas[i]
                i += 1

                if re.match(r'^(Requisitos?|Requisito)\s*:', linha, flags=re.I):
                    campo_atual = 'requisito'
                    valor = linha.split(':', 1)[1].strip()
                    if valor:
                        campos_multilinha['requisito'].append(valor)
                    continue

                if re.match(r'^Mana\s*:', linha, flags=re.I):
                    campo_atual = None
                    habilidade["mana"] = linha.split(':', 1)[1].strip()
                    continue

                if re.match(r'^Ra[cç]as?\s*:', linha, flags=re.I):
                    campo_atual = None
                    habilidade["racas"] = linha.split(':', 1)[1].strip()
                    continue

                if re.match(r'^(Dificuldade|Dificuldade da Magia)\s*:', linha, flags=re.I):
                    campo_atual = None
                    habilidade["dificuldade"] = linha.split(':', 1)[1].strip()
                    continue

                if re.match(r'^Especial\s*:', linha, flags=re.I):
                    campo_atual = 'especial'
                    valor = linha.split(':', 1)[1].strip()
                    if valor:
                        campos_multilinha['especial'].append(valor)
                    continue

                if re.match(r'^(Descricao|Descrição)\s*:', linha, flags=re.I):
                    campo_atual = 'descricao'
                    valor = linha.split(':', 1)[1].strip()
                    if valor:
                        campos_multilinha['descricao'].append(valor)
                    continue

                if campo_atual in ('requisito', 'descricao', 'especial'):
                    if re.match(r'^(Requisitos?|Requisito|Mana|Ra[cç]as?|Dificuldade|Especial|Descricao|Descrição)\s*:', linha, flags=re.I):
                        campo_atual = None
                        i -= 1
                        continue
                    campos_multilinha[campo_atual].append(linha)
                    continue

                detalhes = re.findall(r'\[([^\]]+)\]', linha)
                for detalhe in detalhes:
                    habilidade["detalhes_adicionais"].append(detalhe.strip())

            habilidade["requisito"] = ' '.join(campos_multilinha['requisito']).strip()
            habilidade["descricao"] = ' '.join(campos_multilinha['descricao']).strip()
            habilidade["especial"] = ' '.join(campos_multilinha['especial']).strip()

            habilidades.append(habilidade)

        return habilidades

    def _processar_habilidades(self, conteudo, arquivo_nome):
        """Processa arquivo de habilidades e retorna estrutura categorizada."""
        arquivo_lower = arquivo_nome.lower()

        if "hab_racas" in arquivo_lower:
            return {"Raças": self._extrair_racas(conteudo)}
        if "hab_classes" in arquivo_lower:
            return {"Classes": self._extrair_classes(conteudo)}
        if "hab_caminhos" in arquivo_lower:
            return {"Caminhos": self._extrair_caminhos(conteudo)}

        itens = self._extrair_habilidades(conteudo)
        if "geral" in arquivo_lower:
            for hab in itens:
                hab["eh_geral"] = True
        return {"Habilidades Gerais": itens}

    def _inicializar_pasta_habilidades(self, pasta_nome):
        if pasta_nome not in self.database["habilidades"]:
            self.database["habilidades"][pasta_nome] = {
                "Habilidades Gerais": [],
                "Raças": [],
                "Classes": [],
                "Caminhos": []
            }
            self.database["metadata"]["pastas"].append(pasta_nome)
            self.database["metadata"]["total_pastas"] += 1

    def _contar_habilidades(self):
        total = 0
        for pasta_data in self.database["habilidades"].values():
            if isinstance(pasta_data, dict):
                for categoria_items in pasta_data.values():
                    if isinstance(categoria_items, list):
                        total += len(categoria_items)
            elif isinstance(pasta_data, list):
                total += len(pasta_data)
        return total

    def construir_database(self):
        """Constrói a database a partir da estrutura de pastas."""
        if not os.path.exists(self.arquivos_dir):
            print(f"❌ Pasta '{self.arquivos_dir}' não encontrada!")
            return False

        # Percorre as subpastas
        for pasta_nome in os.listdir(self.arquivos_dir):
            caminho_pasta = os.path.join(self.arquivos_dir, pasta_nome)
            
            # Processa arquivo na raiz da pasta arquivos
            if not os.path.isdir(caminho_pasta):
                if pasta_nome.endswith('.txt'):
                    tipo = self._classificar_arquivo(pasta_nome)
                    conteudo = self._ler_arquivo(os.path.join(self.arquivos_dir, pasta_nome))
                    items = None

                    if tipo == "habilidades":
                        self._inicializar_pasta_habilidades("raiz")
                        items = self._processar_habilidades(conteudo, pasta_nome)
                        for categoria, lista in items.items():
                            if lista:
                                self.database["habilidades"]["raiz"].setdefault(categoria, [])
                                self.database["habilidades"]["raiz"][categoria].extend(lista)
                        self.database["metadata"]["total_arquivos"] += 1

                continue

            # Processa subpasta
            print(f"Processando: {pasta_nome}")
            
            # Percorre os arquivos na subpasta
            for arquivo_nome in os.listdir(caminho_pasta):
                caminho_arquivo = os.path.join(caminho_pasta, arquivo_nome)
                
                if not arquivo_nome.endswith('.txt'):
                    continue

                tipo = self._classificar_arquivo(arquivo_nome)
                conteudo = self._ler_arquivo(caminho_arquivo)
                items = None

                if tipo == "habilidades":
                    self._inicializar_pasta_habilidades(pasta_nome)
                    items = self._processar_habilidades(conteudo, arquivo_nome)
                    for categoria, lista in items.items():
                        if lista:
                            self.database["habilidades"][pasta_nome].setdefault(categoria, [])
                            self.database["habilidades"][pasta_nome][categoria].extend(lista)
                    self.database["metadata"]["total_arquivos"] += 1

                if items is not None:
                    item_count = sum(len(v) for v in items.values() if isinstance(v, list)) if isinstance(items, dict) else len(items)
                    print(f"  {tipo:15} | {arquivo_nome} ({item_count} items)")

        return True

    def salvar_database(self, nome_arquivo="database.json"):
        """Salva a database de habilidades e os JSONs de itens e textos."""
        try:
            with open(nome_arquivo, 'w', encoding='utf-8') as f:
                json.dump(self.database, f, indent=4, ensure_ascii=False)

            print(f"\nDatabase de habilidades salva em: {nome_arquivo}")
            
            self.database['metadata']['total_pastas'] = len(self.database['habilidades'])
            total_habilidades = self._contar_habilidades()
            
            print("Estatisticas:")
            print(f"   - Pastas de habilidades: {self.database['metadata']['total_pastas']}")
            print(f"   - Arquivos de habilidades: {self.database['metadata']['total_arquivos']}")
            print(f"   - Habilidades: {total_habilidades}")
            return True
        except Exception as e:
            print(f"Erro ao salvar database: {e}")
            return False

if __name__ == "__main__":
    # Cria a builder e processa
    builder = DatabaseBuilder(arquivos_dir="arquivos")
    
    if builder.construir_database():
        # Salva a database completa
        builder.salvar_database("database.json")
        
        print("\n" + "="*50)
        print("✨ Base de dados construída com sucesso!")
        print("="*50)
    else:
        print("❌ Falha ao construir database.")

