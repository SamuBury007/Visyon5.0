#!/usr/bin/env python3
"""
VixSrc M3U8 Extractor v4 - Cattura il link playlist di vixsrc.to con interfaccia VISYON
"""

import re
import sys
import json
import asyncio
import requests
from urllib.parse import urlparse, parse_qs, urlencode
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


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
        
        # Intercetta TUTTE le richieste
        async def handle_request(request):
            url = request.url
            # Cerchiamo SOLO i link /playlist/ su vixsrc.to
            if "/playlist/" in url and "vixsrc.to" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] PLAYLIST: {url}")
            
            # Anche se ha /playlist/ in altri formati
            if "playlist" in url and "m3u8" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] M3U8: {url}")
        
        async def handle_response(response):
            url = response.url
            # Cattura anche i redirect che portano a playlist
            if "/playlist/" in url and "vixsrc.to" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] PLAYLIST (resp): {url}")
        
        page.on("request", handle_request)
        page.on("response", handle_response)
        
        print(f"[*] Caricamento: {movie_url}")
        print("[*] In attesa di richieste a /playlist/...")
        
        try:
            await page.goto(movie_url, wait_until="networkidle", timeout=30000)
            # Aspetta più a lungo per catturare tutto
            for i in range(15):
                await asyncio.sleep(1)
                if playlist_urls:
                    print(f"   [+] Già trovati {len(playlist_urls)} link playlist")
        except Exception as e:
            print(f"[-] Timeout, ma continuo...")
            await asyncio.sleep(5)
        
        # Prova anche con evaluate per estrarre direttamente dal JS
        try:
            print("[*] Provo estrazione dal JavaScript...")
            js_result = await page.evaluate("""
                () => {
                    const results = [];
                    // Cerca in tutti gli script tag
                    document.querySelectorAll('script').forEach(s => {
                        const text = s.textContent || '';
                        // Cerca URL con /playlist/
                        const matches = text.match(/https?:\\/\\/[^'"\\s]*\\/playlist\\/[^'"\\s]*/g);
                        if (matches) results.push(...matches);
                        // Cerca URL vixsrc.to/playlist
                        const matches2 = text.match(/vixsrc\\.to\\/playlist\\/[^'"\\s,&]*/g);
                        if (matches2) results.push(...matches2.map(u => 'https://' + u));
                    });
                    // Cerca nel DOM
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
                # Normalizza: se inizia con // o è relativo
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


def _fetch_m3u8(url, referer):
    """Scarica il contenuto testuale di un m3u8, con gli header giusti
    (molte CDN di vixsrc richiedono un Referer valido)."""
    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": referer,
                "Accept": "*/*",
            },
            timeout=10,
        )
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"[-] Errore nel fetch di {url}: {e}")
    return None


def _playlist_has_audio(content):
    """Un master playlist HLS valido con audio contiene tipicamente:
    - una o più righe EXT-X-MEDIA:TYPE=AUDIO  (traccia audio separata)
    - oppure CODECS="...,mp4a..." dentro EXT-X-STREAM-INF (audio muxato)
    Se manca entrambo, è quasi certamente un sotto-playlist solo video."""
    if not content:
        return False
    if "EXT-X-MEDIA:TYPE=AUDIO" in content.upper():
        return True
    if "MP4A" in content.upper():
        return True
    return False


def _is_master_playlist(content):
    """Un master playlist elenca varianti (#EXT-X-STREAM-INF), a differenza
    di un media playlist che elenca direttamente i segmenti (#EXTINF)."""
    return bool(content) and "#EXT-X-STREAM-INF" in content.upper()


async def get_best_playlist(movie_url):
    """Trova la playlist migliore, verificando il CONTENUTO del file m3u8
    (non solo il nome dell'URL) per essere sicuri che includa l'audio."""
    urls = await extract_playlist_url(movie_url)

    if not urls:
        return None

    print(f"\n[*] Trovati {len(urls)} link playlist:")
    for u in urls:
        print(f"   - {u}")

    vixsrc_playlists = [u for u in urls if "vixsrc.to/playlist/" in u]
    candidates = vixsrc_playlists if vixsrc_playlists else urls

    # 1) Cerchiamo tra i candidati un MASTER playlist con audio dichiarato:
    #    è la scelta più sicura, hls.js sceglierà da solo la variante migliore
    #    mantenendo la traccia audio collegata.
    master_with_audio = None
    any_with_audio = None
    for u in candidates:
        content = _fetch_m3u8(u, referer=movie_url)
        if content is None:
            continue
        has_audio = _playlist_has_audio(content)
        is_master = _is_master_playlist(content)
        print(f"   [check] master={is_master} audio={has_audio} -> {u}")
        if has_audio and any_with_audio is None:
            any_with_audio = u
        if has_audio and is_master:
            master_with_audio = u
            break  # trovato il candidato migliore possibile

    if master_with_audio:
        return master_with_audio
    if any_with_audio:
        return any_with_audio

    # 2) Fallback: nessun controllo sul contenuto è riuscito (es. richieste
    #    bloccate dalla CDN): restituiamo comunque il primo link trovato,
    #    meglio di niente, ma segnaliamo il problema nei log.
    print("[!] Impossibile verificare la presenza dell'audio nei playlist trovati, uso il primo disponibile")
    return candidates[0] if candidates else (urls[0] if urls else None)


# ============================================================
# Flask Routes
# ============================================================

@app.route('/')
def index():
    # Carica direttamente il file visyon.html posizionato dentro la cartella /templates
    return render_template('visyon.html')


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
            return jsonify({
                'success': True,
                'url': playlist_url,
            })
        else:
            return jsonify({'success': False, 'error': 'Nessun link playlist trovato.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


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
        
        async def run_main():
            await main()
            
        asyncio.run(run_main())
    else:
        print("""
╔══════════════════════════════════════════════╗
║        VISYON & VixSrc Extractor Server      ║
║──────────────────────────────────────────────║
║  Web UI: http://localhost:8080               ║
╚══════════════════════════════════════════════╝
        """)
        app.run(host='0.0.0.0', port=8080, debug=True)