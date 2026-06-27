#!/usr/bin/env python3
"""
VixSrc M3U8 Extractor v4 - Cattura il link playlist di vixsrc.to con interfaccia VISYON
"""

import re
import sys
import json
import asyncio
import requests
from urllib.parse import urlparse, parse_qs, urlencode, urljoin
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
VIXSRC_REFERER = "https://vixsrc.to/"
VIXSRC_ORIGIN  = "https://vixsrc.to"

# ── Nessun proxy uscente (Webshare Free non supporta HTTPS) ─────────────
# Il token vixsrc è legato all'IP di Railway: Playwright e il proxy /proxy
# devono girare sullo stesso processo Flask → stesso IP → nessun 403.
_OUTBOUND_PROXIES      = None
_OUTBOUND_PROXIES_HTTPS = None


async def extract_playlist_url(movie_url):
    """
    Usa Playwright per catturare SPECIFICAMENTE le richieste
    a vixsrc.to/playlist/... che sono i link M3U8 funzionanti
    """
    playlist_urls = []
    
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()
        
        async def handle_request(req):
            url = req.url
            if "/playlist/" in url and "vixsrc.to" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] PLAYLIST: {url}")
            if "playlist" in url and "m3u8" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] M3U8: {url}")
        
        async def handle_response(response):
            url = response.url
            if "/playlist/" in url and "vixsrc.to" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] PLAYLIST (resp): {url}")
        
        page.on("request", handle_request)
        page.on("response", handle_response)
        
        print(f"[*] Caricamento: {movie_url}")
        try:
            await page.goto(movie_url, wait_until="networkidle", timeout=30000)
            for i in range(15):
                await asyncio.sleep(1)
                if playlist_urls:
                    print(f"   [+] Già trovati {len(playlist_urls)} link playlist")
        except Exception as e:
            print(f"[-] Timeout, ma continuo...")
            await asyncio.sleep(5)
        
        try:
            print("[*] Provo estrazione dal JavaScript...")
            js_result = await page.evaluate("""
                () => {
                    const results = [];
                    document.querySelectorAll('script').forEach(s => {
                        const text = s.textContent || '';
                        const matches = text.match(/https?:\\/\\/[^'"\\s]*\\/playlist\\/[^'"\\s]*/g);
                        if (matches) results.push(...matches);
                        const matches2 = text.match(/vixsrc\\.to\\/playlist\\/[^'"\\s,&]*/g);
                        if (matches2) results.push(...matches2.map(u => 'https://' + u));
                    });
                    const all = document.querySelectorAll('*');
                    all.forEach(el => {
                        if (el.src && el.src.includes('/playlist/')) results.push(el.src);
                        if (el.href && el.href.includes('/playlist/')) results.push(el.href);
                        if (el.data && typeof el.data === 'string' && el.data.includes('/playlist/')) results.push(el.data);
                    });
                    return [...new Set(results)];
                }
            """)
            for url in js_result:
                if url.startswith("//"):
                    url = "https:" + url
                elif url.startswith("/"):
                    url = "https://vixsrc.to" + url
                if url not in playlist_urls and ("/playlist/" in url):
                    playlist_urls.append(url)
                    print(f"[+] Da JS: {url}")
        except Exception as e:
            print(f"[-] JS extraction: {e}")
        
        await browser.close()
    
    return playlist_urls


# Sessione globale: mantiene lo stesso IP di uscita per tutte le richieste
_SESSION = requests.Session()

# Cache: conserva l'ultimo contenuto M3U8 scaricato e il suo URL originale
_m3u8_cache = {}  # { url: contenuto_testuale }

def _vixsrc_headers(referer=VIXSRC_REFERER):
    """Header HTTP che il CDN di vixsrc.to si aspetta."""
    return {
        "User-Agent": USER_AGENT,
        "Referer":    referer,
        "Origin":     VIXSRC_ORIGIN,
        "Accept":     "*/*",
    }


def _fetch_m3u8(url, referer=VIXSRC_REFERER):
    """Scarica il contenuto testuale di un m3u8 con gli header corretti e lo cacha."""
    try:
        r = _SESSION.get(url, headers=_vixsrc_headers(referer), timeout=10)
        if r.status_code == 200:
            _m3u8_cache[url] = r.text
            return r.text
        print(f"[-] HTTP {r.status_code} per {url}")
    except Exception as e:
        print(f"[-] Errore nel fetch di {url}: {e}")
    return None


def _playlist_has_audio(content):
    if not content:
        return False
    if "EXT-X-MEDIA:TYPE=AUDIO" in content.upper():
        return True
    if "MP4A" in content.upper():
        return True
    return False


def _is_master_playlist(content):
    return bool(content) and "#EXT-X-STREAM-INF" in content.upper()


async def get_best_playlist(movie_url):
    urls = await extract_playlist_url(movie_url)
    if not urls:
        return None

    print(f"\n[*] Trovati {len(urls)} link playlist:")
    for u in urls:
        print(f"   - {u}")

    vixsrc_playlists = [u for u in urls if "vixsrc.to/playlist/" in u]
    candidates = vixsrc_playlists if vixsrc_playlists else urls

    master_with_audio = None
    any_with_audio    = None
    for u in candidates:
        content  = _fetch_m3u8(u, referer=movie_url)
        has_audio = _playlist_has_audio(content)
        is_master = _is_master_playlist(content)
        print(f"   [check] master={is_master} audio={has_audio} -> {u}")
        if has_audio and any_with_audio is None:
            any_with_audio = u
        if has_audio and is_master:
            master_with_audio = u
            break

    if master_with_audio:
        return master_with_audio
    if any_with_audio:
        return any_with_audio

    print("[!] Impossibile verificare l'audio, uso il primo disponibile")
    return candidates[0] if candidates else (urls[0] if urls else None)


# ============================================================
# Helper: riscrive i segmenti relativi dentro un M3U8
# ============================================================

def _rewrite_m3u8(content, original_url, proxy_base):
    """
    Trasforma tutti gli URI dentro il testo M3U8 in chiamate al proxy:
    - righe URI (segmenti .ts, sotto-playlist)
    - URI dentro #EXT-X-KEY (chiave AES-128)
    """
    from urllib.parse import quote
    import re
    base = original_url.rsplit("/", 1)[0] + "/"

    def to_proxy(url):
        if not (url.startswith("http://") or url.startswith("https://")):
            url = urljoin(base, url)
        return f"{proxy_base}/proxy?url={quote(url, safe='')}"

    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "":
            lines.append(line)
        elif stripped.startswith("#"):
            # Riscrive TUTTI gli URI="..." in qualsiasi tag (#EXT-X-KEY, #EXT-X-MEDIA, ecc.)
            new_line = re.sub(r'URI="([^"]+)"', lambda m: f'URI="{to_proxy(m.group(1))}"'  , line)
            lines.append(new_line)
        else:
            lines.append(to_proxy(stripped))
    return "\n".join(lines)


# ============================================================
# Flask Routes
# ============================================================

@app.route('/')
def index():
    return open('visyon.html').read()


@app.route('/extract', methods=['POST'])
def api_extract():
    data = request.get_json()
    movie_url = data.get('url', '')
    
    if not movie_url:
        return jsonify({'success': False, 'error': 'URL richiesto'})
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        playlist_url = loop.run_until_complete(get_best_playlist(movie_url))
        loop.close()
        
        if playlist_url:
            # Forza lingua italiana
            playlist_url_it = playlist_url.replace('lang=en', 'lang=it')
            cached_content = _m3u8_cache.get(playlist_url, _m3u8_cache.get(playlist_url_it, ''))
            # Aggiorna cache con URL italiano
            if cached_content:
                _m3u8_cache[playlist_url_it] = cached_content
            return jsonify({
                'success': True,
                'url': playlist_url_it,
                'm3u8_content': cached_content,
            })
        else:
            return jsonify({'success': False, 'error': 'Nessun link playlist trovato.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/proxy')
def proxy_m3u8():
    """
    Proxy trasparente per i contenuti vixsrc.to.
    Uso:  GET /proxy?url=<url_assoluto>
    """
    from urllib.parse import unquote
    # request.args decodifica automaticamente %3D → = e %26 → &
    # ma se il frontend ha encodato due volte, dobbiamo fare unquote manuale
    target_url = request.args.get('url', '')
    if not target_url:
        return jsonify({'error': 'Parametro url mancante'}), 400

    # Decodifica doppio encoding se presente
    if '%25' in target_url or '%3D' in target_url:
        target_url = unquote(target_url)

    print(f"[proxy] → {target_url}")

    # Determina il Referer in base all'host target
    parsed = urlparse(target_url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"

    try:
        upstream = _SESSION.get(
            target_url,
            headers=_vixsrc_headers(referer),
            timeout=20,
            stream=True,
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 502

    content_type = upstream.headers.get('Content-Type', 'application/octet-stream')
    is_m3u8 = (
        'mpegurl' in content_type.lower()
        or target_url.endswith('.m3u8')
        or (upstream.status_code == 200 and upstream.text[:7] == '#EXTM3U')
    )

    if upstream.status_code != 200:
        return Response(
            upstream.content,
            status=upstream.status_code,
            content_type=content_type,
        )

    if is_m3u8:
        # Leggi tutto il testo e riscrivi gli URI
        raw_text   = upstream.text
        # Usa X-Forwarded-Host se presente (Railway / reverse proxy)
        forwarded_host  = request.headers.get('X-Forwarded-Host')
        forwarded_proto = request.headers.get('X-Forwarded-Proto', 'https')
        if forwarded_host:
            proxy_base = f"{forwarded_proto}://{forwarded_host}"
        else:
            proxy_base = request.host_url.rstrip('/')
        rewritten  = _rewrite_m3u8(raw_text, target_url, proxy_base)
        response   = Response(rewritten, content_type='application/vnd.apple.mpegurl')
    else:
        # Passa i byte così come sono (segmenti .ts, chiavi AES, ecc.)
        response = Response(
            upstream.iter_content(chunk_size=8192),
            content_type=content_type,
        )

    # Header CORS — indispensabili per HLS.js nel browser
    response.headers['Access-Control-Allow-Origin']  = '*'
    response.headers['Access-Control-Allow-Headers'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    return response


@app.route('/proxy', methods=['OPTIONS'])
def proxy_options():
    """Preflight CORS."""
    r = Response('', status=204)
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Headers'] = '*'
    r.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    return r


# ============================================================
# Main
# ============================================================

if __name__ == '__main__':
    if len(sys.argv) > 1:
        async def main():
            movie_url = sys.argv[1]
            print(f"[*] Estrazione da: {movie_url}")
            url = await get_best_playlist(movie_url)
            if url:
                print(f"\n[+] Link playlist:\n    {url}")
            else:
                print("\n[-] Nessuna playlist trovata")
        asyncio.run(main())
    else:
        print("""
╔══════════════════════════════════════════════╗
║        VISYON & VixSrc Extractor Server      ║
║──────────────────────────────────────────────║
║  Web UI: http://localhost:8080               ║
╚══════════════════════════════════════════════╝
        """)
        app.run(host='0.0.0.0', port=8080, debug=True)
