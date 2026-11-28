import os
import re
import sys
import json
import time
from urllib.parse import urlparse, urljoin
import firebase_admin
from firebase_admin import credentials, db
from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import stealth_sync
except ImportError:
    stealth_sync = None

FIREBASE_DB_URL = (
    os.environ.get("FIREBASE_DB_URL")
    or "https://k404repacks-pc-default-rtdb.firebaseio.com"
).rstrip("/")
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON")
FORCE_UPDATE = os.environ.get("FORCE_UPDATE", "").strip().lower() in ("1", "true", "yes")
PASTA_BASE = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_JOGOS = os.path.join(PASTA_BASE, "jogos.txt")

DOMINIOS_IGNORADOS = (
    "rexagames.com",
    "doubleclick.net",
    "googlesyndication",
    "google-analytics",
    "googletagmanager",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "discord.gg",
    "discord.com",
    "store.steampowered.com",
    "cdn-cgi",
    "javascript:",
)


def obter_ref_firebase(id_jogo):
    if not FIREBASE_CREDENTIALS_JSON:
        raise RuntimeError("FIREBASE_CREDENTIALS_JSON não configurado nos Secrets do GitHub.")

    if not firebase_admin._apps:
        info = json.loads(FIREBASE_CREDENTIALS_JSON)
        cred = credentials.Certificate(info)
        firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

    return db.reference(f"jogos/{id_jogo}")


def gerar_id_jogo(url_origem):
    path = urlparse(url_origem).path.strip("/")
    partes = [p for p in path.split("/") if p and p.lower() != "games"]
    slug = partes[-1] if partes else "jogo-rexagames"
    slug = re.sub(r"\.(html|php)$", "", slug, flags=re.IGNORECASE).lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
    return slug or "jogo-rexagames"


def limpar_nome_jogo(titulo_raw, id_jogo):
    t = (titulo_raw or "").strip()
    t = re.sub(r"\s*[\|\-–—]\s*RexaGames.*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s*Free\s*Download.*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s+", " ", t).strip()
    if t and t.lower() not in ("rexagames", "home"):
        return t
    return id_jogo.replace("-", " ").title()


def extrair_campo_rotulado(texto_corpo, rotulos):
    if not texto_corpo:
        return ""
    for rot in rotulos:
        m = re.search(
            rf"(?:^|\n|\s){re.escape(rot)}\s*[:\-]?\s*([^\n\r]+)",
            texto_corpo,
            flags=re.IGNORECASE,
        )
        if m:
            val = re.sub(r"\s+", " ", m.group(1)).strip()
            if val:
                return val
    return ""


def extrair_versao(titulo_completo, texto_corpo):
    val_rot = extrair_campo_rotulado(texto_corpo, ["VERSION", "Version"])
    if val_rot:
        m = re.search(r"v?\d+(?:\.\d+){1,4}", val_rot, flags=re.IGNORECASE)
        if m:
            v = m.group(0)
            return v if v.lower().startswith("v") else "v" + v
        return val_rot

    for fonte in (titulo_completo or "", texto_corpo or ""):
        m = re.search(r"\bv\d+(?:\.\d+){1,4}\b", fonte, flags=re.IGNORECASE)
        if m:
            return m.group(0)
    return ""


def extrair_tamanho(texto_corpo):
    val_rot = extrair_campo_rotulado(texto_corpo, ["SIZE", "Game Size", "Tamanho"])
    if val_rot:
        m = re.search(r"[0-9]+(?:[.,][0-9]+)?\s*(?:GB|MB|TB)", val_rot, flags=re.IGNORECASE)
        if m:
            return re.sub(r"\s+", " ", m.group(0)).replace(",", ".").strip()
    return ""


def extrair_extra(texto_corpo):
    extras = []
    source_val = extrair_campo_rotulado(texto_corpo, ["SOURCE", "Source"])
    if source_val and len(source_val) <= 35:
        extras.append(source_val)

    for pat in (
        r"\+\s*Multiplayer(?:\s*Fix)?",
        r"\+\s*Online(?:\s*Fix)?",
        r"\+\s*All\s*(?:DLCs?|add-ons?)",
        r"All\s*DLCs?",
        r"Deluxe\s*Edition",
        r"Complete\s*Edition",
        r"Gold\s*Edition",
        r"Ultimate\s*Edition",
    ):
        for achado in re.findall(pat, texto_corpo or "", flags=re.IGNORECASE):
            limpo = re.sub(r"\s+", " ", achado).strip()
            if limpo.lower() not in [x.lower() for x in extras]:
                extras.append(limpo)

    return " ".join(extras).strip()


def url_parece_anuncio_ou_invalida(url):
    if not url or not url.startswith(("http://", "https://", "magnet:")):
        return True
    u = url.lower().strip()
    if any(bad in u for bad in DOMINIOS_IGNORADOS):
        return True
    if any(p in u for p in ("/error", "referrer", "vpn", "adblock", "popup")):
        return True
    return False


def normalizar_chave_servidor(nome_srv, url=""):
    base = (nome_srv or "").strip()
    if not base and url:
        try:
            host = urlparse(url).netloc.lower().replace("www.", "")
            base = host.split(".")[0] if host else "mirror"
        except Exception:
            base = "mirror"
    chave = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")
    return chave or "mirror"


def fechar_abas_anuncio(context, paginas_seguras):
    for p in list(context.pages):
        if p not in paginas_seguras:
            try:
                p.close()
            except Exception:
                pass


def extrair_links_dentro_zeilink(context, zeilink_url, paginas_seguras):
    links_extraidos = {}
    aba = None

    try:
        aba = context.new_page()
        aba.set_default_timeout(25000)
        aba.goto(zeilink_url, wait_until="domcontentloaded", timeout=45000)
        aba.wait_for_timeout(3500)
        fechar_abas_anuncio(context, paginas_seguras | {aba})

        try:
            aba.evaluate(
                """() => {
                    const cb = document.querySelector('input[type="checkbox"]');
                    if (cb && !cb.checked) cb.click();
                }"""
            )
            aba.wait_for_timeout(500)
        except Exception:
            pass

        nomes_servidores = aba.evaluate(
            """() => {
                const out = [];
                const els = Array.from(document.querySelectorAll('button, [role="tab"], a, div, span'));
                for (const el of els) {
                    const txt = (el.innerText || el.textContent || '').trim();
                    if (!txt || txt.length > 35) continue;
                    if (/^(filesyard|datanodes|fileditch|vikingfile|datavaults|fileq|1fichier|gofile|pixeldrain|buzzheavier|megadb)(\s*\d+)?$/i.test(txt)) {
                        const limpo = txt.replace(/\\s*\\d+$/, '').trim();
                        if (!out.includes(limpo)) out.push(limpo);
                    }
                }
                return out;
            }"""
        ) or []

        if not nomes_servidores:
            nomes_servidores = [""]

        for srv in nomes_servidores:
            if srv:
                try:
                    btn_srv = aba.get_by_text(re.compile(rf"^\s*{re.escape(srv)}\s*\d*\s*$", re.I)).first
                    if btn_srv.count() > 0:
                        btn_srv.click(timeout=4000)
                        aba.wait_for_timeout(1200)
                        fechar_abas_anuncio(context, paginas_seguras | {aba})
                except Exception:
                    pass

            hrefs_diretos = aba.evaluate(
                """() => {
                    const urls = [];
                    document.querySelectorAll('a[href]').forEach(a => {
                        const h = (a.getAttribute('href') || '').trim();
                        if (h.startsWith('http')) urls.push(h);
                    });
                    return urls;
                }"""
            ) or []

            url_servidor = ""
            for h in hrefs_diretos:
                if "zeilink.net" not in h.lower() and not url_parece_anuncio_ou_invalida(h):
                    url_servidor = h
                    break

            if not url_servidor:
                antes_p = list(context.pages)
                url_antes = aba.url
                try:
                    dl_btn = aba.locator(
                        "a:has-text('Descarregar'), button:has-text('Descarregar'), a:has-text('Download'), button:has-text('Download')"
                    ).last
                    if dl_btn.count() > 0:
                        dl_btn.click(timeout=4000)
                        prazo = time.time() + 8.0
                        while time.time() < prazo:
                            aba.wait_for_timeout(700)
                            for p_extra in list(context.pages):
                                if p_extra not in antes_p and p_extra != aba:
                                    u_aba = (p_extra.url or "").strip()
                                    if "zeilink.net" not in u_aba.lower() and not url_parece_anuncio_ou_invalida(u_aba):
                                        url_servidor = u_aba
                                    try:
                                        p_extra.close()
                                    except Exception:
                                        pass
                            if url_servidor:
                                break
                            if aba.url and aba.url != url_antes and "zeilink.net" not in aba.url.lower():
                                if not url_parece_anuncio_ou_invalida(aba.url):
                                    url_servidor = aba.url
                                    break
                except Exception:
                    pass

                if aba.url != zeilink_url:
                    try:
                        aba.goto(zeilink_url, wait_until="domcontentloaded", timeout=35000)
                        aba.wait_for_timeout(2000)
                    except Exception:
                        break

            if url_servidor and not url_parece_anuncio_ou_invalida(url_servidor):
                chave_base = normalizar_chave_servidor(srv, url_servidor)
                if chave_base not in links_extraidos:
                    links_extraidos[chave_base] = url_servidor
                else:
                    idx = 2
                    while f"{chave_base}_{idx}" in links_extraidos:
                        idx += 1
                    links_extraidos[f"{chave_base}_{idx}"] = url_servidor

    except Exception as e:
        print(f"⚠️ Erro ao ler ZeiLink '{zeilink_url}': {e}")
    finally:
        if aba is not None:
            try:
                aba.close()
            except Exception:
                pass

    return links_extraidos


def extrair_dados_rexagames(url_alvo):
    print(f"🔎 Extraindo RexaGames: {url_alvo}")
    id_jogo = gerar_id_jogo(url_alvo)

    resultado = {
        "id": id_jogo,
        "url_origem": url_alvo,
        "nome": id_jogo.replace("-", " ").title(),
        "versao": "",
        "extra": "",
        "tamanho": "",
        "foto": "",
        "links": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--window-size=1366,768",
            ],
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )

        page = context.new_page()
        if stealth_sync:
            stealth_sync(page)
        else:
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

        paginas_seguras = {page}

        try:
            page.goto(url_alvo, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(4000)
            fechar_abas_anuncio(context, paginas_seguras)

            titulo_completo = ""
            try:
                h1 = page.locator("h1").first
                if h1.count() > 0:
                    titulo_completo = (h1.inner_text() or "").strip()
            except Exception:
                pass

            if not titulo_completo:
                titulo_completo = (page.title() or "").strip()

            texto_corpo = ""
            try:
                texto_corpo = page.evaluate(
                    """() => {
                        const root = document.querySelector('main, article, .container, body');
                        return root ? (root.innerText || '') : '';
                    }"""
                ) or ""
            except Exception:
                texto_corpo = ""

            resultado["nome"] = limpar_nome_jogo(titulo_completo, id_jogo)
            resultado["versao"] = extrair_versao(titulo_completo, texto_corpo)
            resultado["tamanho"] = extrair_tamanho(texto_corpo)
            resultado["extra"] = extrair_extra(texto_corpo)

            foto_url = ""
            try:
                og_img = page.locator('meta[property="og:image"]').first
                if og_img.count() > 0:
                    foto_url = (og_img.get_attribute("content") or "").strip()
            except Exception:
                pass

            if not foto_url:
                try:
                    imgs = page.eval_on_selector_all(
                        "main img, article img, img",
                        """els => els.map(e => e.getAttribute('src') || e.getAttribute('data-src') || '')""",
                    )
                    for src in imgs:
                        s = (src or "").strip()
                        if s.startswith("http") and "logo" not in s.lower() and "icon" not in s.lower():
                            foto_url = s
                            break
                except Exception:
                    pass

            if foto_url:
                resultado["foto"] = urljoin(url_alvo, foto_url)

            btn_dl_jogo = page.locator(
                "button:has-text('Download'), a:has-text('Download')"
            ).first
            if btn_dl_jogo.count() > 0:
                btn_dl_jogo.scroll_into_view_if_needed(timeout=4000)
                btn_dl_jogo.click(timeout=5000)
                page.wait_for_timeout(2000)
                fechar_abas_anuncio(context, paginas_seguras)

            zeilink_url = ""
            antes_paginas = list(context.pages)

            zeilink_url = page.evaluate(
                """() => {
                    for (const a of document.querySelectorAll('a[href]')) {
                        const h = (a.getAttribute('href') || '').trim();
                        if (h.includes('zeilink.net')) return h;
                    }
                    return '';
                }"""
            ) or ""

            if not zeilink_url:
                modal_dl = page.locator(
                    "[role='dialog'] button:has-text('Download'), [role='dialog'] a:has-text('Download'), .modal button:has-text('Download'), .modal a:has-text('Download')"
                ).first
                if modal_dl.count() == 0:
                    todos_dl = page.locator("button:has-text('Download'), a:has-text('Download')")
                    if todos_dl.count() > 1:
                        modal_dl = todos_dl.nth(1)

                if modal_dl.count() > 0:
                    try:
                        modal_dl.click(timeout=5000)
                    except Exception:
                        pass

                    prazo = time.time() + 10.0
                    while time.time() < prazo:
                        page.wait_for_timeout(700)
                        for p_nova in list(context.pages):
                            if p_nova not in antes_paginas:
                                u_nova = (p_nova.url or "").strip()
                                if "zeilink.net" in u_nova.lower():
                                    zeilink_url = u_nova
                                    break
                        if zeilink_url:
                            break

                        zeilink_url = page.evaluate(
                            """() => {
                                for (const a of document.querySelectorAll('a[href]')) {
                                    const h = (a.getAttribute('href') || '').trim();
                                    if (h.includes('zeilink.net')) return h;
                                }
                                return '';
                            }"""
                        ) or ""
                        if zeilink_url:
                            break

            fechar_abas_anuncio(context, paginas_seguras)

            if zeilink_url:
                resultado["links"] = extrair_links_dentro_zeilink(context, zeilink_url, paginas_seguras)
                if not resultado["links"]:
                    resultado["links"] = {"zeilink": zeilink_url}

        except Exception as e:
            print(f"❌ Erro ao extrair '{url_alvo}': {e}")

        browser.close()

    return resultado


def salvar_jogo_firebase(dados_novos):
    id_jogo = dados_novos["id"]
    if not dados_novos.get("links"):
        print(f"⚠️ Nenhum link encontrado para '{id_jogo}'. Nada enviado ao Firebase.")
        return False

    try:
        ref = obter_ref_firebase(id_jogo)
        atual = ref.get() or {}
        if not isinstance(atual, dict):
            atual = {}

        mudou_links = (atual.get("links") or {}) != (dados_novos.get("links") or {})
        mudou_versao = (atual.get("versao") or "") != (dados_novos.get("versao") or "")
        mudou_extra = (atual.get("extra") or "") != (dados_novos.get("extra") or "")
        mudou_nome = (atual.get("nome") or "") != (dados_novos.get("nome") or "")
        mudou_tamanho = (atual.get("tamanho") or "") != (dados_novos.get("tamanho") or "")
        mudou_foto = (atual.get("foto") or "") != (dados_novos.get("foto") or "")

        if not FORCE_UPDATE and not any((
            mudou_links,
            mudou_versao,
            mudou_extra,
            mudou_nome,
            mudou_tamanho,
            mudou_foto,
        )):
            print(
                f"⏩ '{id_jogo}' sem alterações em links, versão ou extra "
                f"({dados_novos.get('versao') or 'sem versão'}). Ignorado."
            )
            return True

        motivos = []
        if mudou_links:
            motivos.append("links")
        if mudou_versao:
            motivos.append("versão")
        if mudou_extra:
            motivos.append("extra")
        if mudou_nome:
            motivos.append("nome")
        if mudou_tamanho:
            motivos.append("tamanho")
        if mudou_foto:
            motivos.append("foto")

        payload = {
            "id": id_jogo,
            "nome": dados_novos["nome"],
            "versao": dados_novos["versao"],
            "extra": dados_novos["extra"],
            "tamanho": dados_novos["tamanho"],
            "foto": dados_novos["foto"],
            "links": dados_novos["links"],
            "url_origem": dados_novos["url_origem"],
            "fonte": "rexagames",
        }

        ref.set(payload)
        print(
            f"✅ Firebase PC atualizado [{', '.join(motivos) or 'forçado'}] -> ID: {id_jogo} | "
            f"Versão: {payload['versao'] or '-'} | "
            f"Extra: {payload['extra'] or '-'} | "
            f"Tamanho: {payload['tamanho'] or '-'} | "
            f"Links: {len(payload['links'])}"
        )
        print("🔗 Links gravados:", json.dumps(payload["links"], indent=2, ensure_ascii=False))
        return True
    except Exception as e:
        print(f"❌ Erro ao salvar no Firebase: {e}")
        return False


def registrar_na_lista(url_alvo):
    pasta = os.path.dirname(ARQUIVO_JOGOS)
    if pasta:
        os.makedirs(pasta, exist_ok=True)

    existentes = set()
    if os.path.exists(ARQUIVO_JOGOS):
        with open(ARQUIVO_JOGOS, "r", encoding="utf-8") as f:
            existentes = {line.strip() for line in f if line.strip()}

    if url_alvo not in existentes:
        with open(ARQUIVO_JOGOS, "a", encoding="utf-8") as f:
            f.write(url_alvo + "\n")
        print(f"📝 Adicionado ao {ARQUIVO_JOGOS}: {url_alvo}")


def processar_url(url_alvo):
    url_limpa = (url_alvo or "").strip()
    if not url_limpa.startswith(("http://", "https://")):
        print(f"⚠️ URL inválida ignorada: {url_alvo}")
        return

    print("\n==================================================")
    dados = extrair_dados_rexagames(url_limpa)
    ok = salvar_jogo_firebase(dados)
    if ok:
        registrar_na_lista(url_limpa)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a not in ("--force", "--no-force")]
    if "--force" in sys.argv[1:]:
        FORCE_UPDATE = True
    elif "--no-force" in sys.argv[1:]:
        FORCE_UPDATE = False

    if args and args[0].strip().startswith("http"):
        processar_url(args[0].strip())
    elif os.path.exists(ARQUIVO_JOGOS):
        with open(ARQUIVO_JOGOS, "r", encoding="utf-8") as f:
            urls = [linha.strip() for linha in f if linha.strip() and not linha.strip().startswith("#")]

        print(f"🤖 Modo automático iniciado. {len(urls)} jogo(s) no {ARQUIVO_JOGOS}.")
        for u in urls:
            processar_url(u)
    else:
        print(f"⚠️ Nenhum '{ARQUIVO_JOGOS}' encontrado e nenhuma URL manual passada.")
