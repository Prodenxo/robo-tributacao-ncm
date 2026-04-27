"""
Robô de consulta tributária no E-Auditoria.
Versão adaptada para uso como módulo importável pelo backend web.
"""
from playwright.sync_api import sync_playwright
import pandas as pd
import re
import os
import io
from difflib import SequenceMatcher


def similaridade(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def extrair_cst_do_titulo(titulo):
    match = re.match(r'^([\d,]+)', titulo.strip())
    return match.group(1) if match else ""


def extrair_dados_do_texto(texto):
    resultado = {
        'PIS_Presumido': '', 'COFINS_Presumido': '',
        'PIS_Real': '', 'COFINS_Real': '',
        'Descricao_SPED': ''
    }
    blocos = re.split(r'Al[ií]quota PIS \([^)]+\):', texto, flags=re.IGNORECASE)

    for bloco in blocos[1:]:
        pis_m = re.search(r'^\s*([\d,.]+)', bloco)
        cof_m = re.search(r'Al[ií]quota COFINS \([^)]+\):\s*([\d,.]+)', bloco, re.IGNORECASE)
        fim_m = re.search(r'Fim:\s*([^\n]+)', bloco)
        desc_m = re.search(r'Descri[çc][aã]o SPED:\s*([^\n]+)', bloco, re.IGNORECASE)

        if pis_m and cof_m and fim_m:
            if "vigente" not in fim_m.group(1).strip().lower():
                continue
            pis, cofins = pis_m.group(1), cof_m.group(1)
            if desc_m and not resultado['Descricao_SPED']:
                resultado['Descricao_SPED'] = desc_m.group(1).strip()

            txt = bloco.lower()
            if "não-cumulativo" in txt or "não cumulativo" in txt:
                resultado['PIS_Real'] = pis
                resultado['COFINS_Real'] = cofins
            elif "cumulativo" in txt:
                resultado['PIS_Presumido'] = pis
                resultado['COFINS_Presumido'] = cofins
            else:
                if not resultado['PIS_Presumido']:
                    resultado['PIS_Presumido'] = pis
                    resultado['COFINS_Presumido'] = cofins
                elif not resultado['PIS_Real']:
                    resultado['PIS_Real'] = pis
                    resultado['COFINS_Real'] = cofins
    return resultado


def processar_planilha(arquivo_bytes, login, senha, callback=None):
    """
    Processa uma planilha Excel com NCMs e retorna os bytes do Excel resultado.
    callback(msg): função opcional para reportar progresso.
    """
    def log(msg):
        try:
            print(msg)
        except UnicodeEncodeError:
            # Se o console não aceitar o emoji, imprime sem ele ou com substituição
            print(msg.encode('ascii', 'replace').decode('ascii'))
            
        if callback:
            callback(msg)

    df = pd.read_excel(io.BytesIO(arquivo_bytes))

    # Detectar coluna NCM
    col_ncm = None
    for c in df.columns:
        if 'ncm' in str(c).lower():
            col_ncm = c
            break
    if col_ncm is None:
        col_ncm = df.columns[0]

    df = df.rename(columns={col_ncm: 'NCM'})
    for col in ['CST', 'PIS_Presumido', 'COFINS_Presumido', 'PIS_Real', 'COFINS_Real']:
        if col not in df.columns:
            df[col] = ""

    ncms_unicos = df['NCM'].dropna().unique()
    log(f"📊 {len(df)} linhas | {len(ncms_unicos)} NCMs únicos")

    cache = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def navegar_com_retry(url, tentativas=3, wait_until='load'):
            for tentativa in range(1, tentativas + 1):
                try:
                    response = page.goto(url, wait_until=wait_until, timeout=30000)
                    if response and response.status and response.status >= 400:
                        raise RuntimeError(f'HTTP {response.status} ao acessar {url}')
                    return
                except Exception as exc:
                    erro = str(exc)
                    ultimo_tentativa = tentativa == tentativas
                    if 'net::ERR_ABORTED' in erro and not ultimo_tentativa:
                        log(f"   ⚠️ Navegação abortada ({tentativa}/{tentativas}), tentando novamente...")
                        page.wait_for_timeout(1200 * tentativa)
                        continue
                    raise RuntimeError(f'Falha ao navegar para {url}: {erro}') from exc

        if not login or not senha:
            raise ValueError('Credenciais E-Auditoria ausentes no .env (EAUDITORIA_LOGIN/EAUDITORIA_SENHA).')

        log("🔑 Fazendo login...")
        navegar_com_retry("https://econsulta.e-auditoria.com.br/")
        page.wait_for_selector('input[name="username"]', timeout=15000)
        page.fill('input[name="username"]', login)
        page.fill('input[name="password"]', senha)
        page.click('input[type="submit"], button[type="submit"]')
        page.wait_for_load_state('domcontentloaded')
        page.wait_for_timeout(1500)

        navegar_com_retry("https://econsulta.e-auditoria.com.br/Home/Consulta")
        page.wait_for_load_state('networkidle')
        log("✅ Login OK!")

        for i, ncm_val in enumerate(ncms_unicos):
            ncm = str(ncm_val).strip()
            if len(ncm) < 4:
                continue

            log(f"🔍 [{i+1}/{len(ncms_unicos)}] NCM: {ncm}")

            try:
                navegar_com_retry("https://econsulta.e-auditoria.com.br/Home/Consulta")
                page.wait_for_load_state('networkidle')
                page.wait_for_timeout(1500)

                busca = page.locator("input[placeholder*='Faça sua pesquisa']")
                busca.fill("")
                busca.fill(ncm)
                page.wait_for_timeout(2000)
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")

                btn = page.get_by_text("PIS/COFINS", exact=True)
                btn.wait_for(state="visible", timeout=10000)
                btn.scroll_into_view_if_needed()
                
                # Verifica se o botão está habilitado (não está cinza/bloqueado)
                if not btn.is_enabled():
                    log(f"   ⚠️ NCM {ncm}: não foi possivel a consulta de NCM")
                    cache[ncm] = []
                    continue

                btn.click(timeout=5000)
                page.wait_for_timeout(2000)
                page.mouse.wheel(0, 500)
                page.wait_for_timeout(1000)

                titulos_raw = page.evaluate("""
                    () => {
                        const lines = document.body.innerText.split('\\n');
                        return lines.filter(l => /^\\d/.test(l.trim()) &&
                            (l.includes('Operação') || l.includes('Crédito')))
                            .map(l => l.trim());
                    }
                """)

                page.evaluate("""
                    () => {
                        document.querySelectorAll('a.icon-collapse-ncm').forEach(a => {
                            const href = a.getAttribute('href');
                            if (href) {
                                const id = href.replace('#', '');
                                const target = document.getElementById(id);
                                if (target) {
                                    target.classList.add('in');
                                    target.classList.add('show');
                                    target.style.display = 'block';
                                    target.style.height = 'auto';
                                }
                            }
                        });
                    }
                """)
                page.wait_for_timeout(2000)

                setinhas = page.locator("a.icon-collapse-ncm")
                total = setinhas.count()
                cst_lista = []

                for s in range(total):
                    titulo = titulos_raw[s] if s < len(titulos_raw) else ""
                    cst_num = extrair_cst_do_titulo(titulo)

                    seta = setinhas.nth(s)
                    href = seta.get_attribute("href") or ""
                    content_id = href.replace("#", "")

                    conteudo = ""
                    if content_id:
                        try:
                            conteudo = page.locator(f"[id='{content_id}']").inner_text(timeout=3000)
                        except:
                            pass
                    if not conteudo:
                        continue

                    dados = extrair_dados_do_texto(conteudo)
                    dados['CST'] = cst_num
                    dados['Titulo'] = titulo

                    if dados['PIS_Presumido'] or dados['PIS_Real']:
                        cst_lista.append(dados)
                        log(f"   ✅ CST {cst_num}: {dados['Descricao_SPED'][:40]}")

                cache[ncm] = cst_lista

            except Exception:
                log(f"   ❌ NCM {ncm}: não foi possivel a consulta de NCM")
                cache[ncm] = []

        browser.close()

    # Preencher resultados
    for idx, row in df.iterrows():
        ncm = str(row['NCM']).strip()
        lista = cache.get(ncm, [])
        if not lista:
            continue

        if len(lista) == 1:
            dados = lista[0]
        else:
            # Se tiver coluna de descrição, usar para fuzzy match
            nome = ""
            for c in df.columns:
                if 'desc' in str(c).lower() or 'nome' in str(c).lower() or 'prod' in str(c).lower():
                    nome = str(row[c]) if pd.notna(row[c]) else ""
                    break
            if nome:
                melhor, melhor_score = lista[0], -1
                for d in lista:
                    score = similaridade(nome, d.get('Descricao_SPED', ''))
                    if score > melhor_score:
                        melhor_score = score
                        melhor = d
                dados = melhor
            else:
                dados = lista[0]

        df.at[idx, 'CST'] = dados['CST']
        df.at[idx, 'PIS_Presumido'] = dados['PIS_Presumido']
        df.at[idx, 'COFINS_Presumido'] = dados['COFINS_Presumido']
        df.at[idx, 'PIS_Real'] = dados['PIS_Real']
        df.at[idx, 'COFINS_Real'] = dados['COFINS_Real']

    # Gerar bytes do Excel resultado
    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    log("🎉 Processamento concluído!")
    return output.getvalue()
