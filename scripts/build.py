#!/usr/bin/env python3
"""Brief audio de Gaël : transforme les scripts d'épisodes (episodes/*.json)
en MP3 et en flux podcast (RSS) publié sur GitHub Pages.

Sous-commandes :
  missing --existing FICHIER        liste les épisodes sans MP3 (un id par ligne)
  synth ID [ID...] --out DOSSIER    fabrique les MP3 de ces épisodes
  keep --keep N                     liste les N épisodes les plus récents (ids)
  site --site DOSSIER --keep N      écrit feed.xml et index.html (les MP3 doivent
                                    déjà être dans DOSSIER/audio)
"""
import argparse
import asyncio
import datetime as dt
import email.utils
import html
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
EPISODES_DIR = ROOT / "episodes"
STATIC_DIR = ROOT / "static"
PARIS = ZoneInfo("Europe/Paris")

SITE_URL = os.environ.get("SITE_URL", "https://gaelgiua-hub.github.io/brief-audio").rstrip("/")
VOICE = os.environ.get("TTS_VOICE", "fr-FR-VivienneMultilingualNeural")
RATE = os.environ.get("TTS_RATE", "+0%")
SHOW_TITLE = "Le brief audio de Gaël"
SHOW_DESC = ("Le brief marchés du matin et le récap de la semaine, en audio. "
             "Bourse, taux, épargne et patrimoine, expliqués simplement. "
             "Informations à titre informatif, pas un conseil en investissement.")
DISCLAIMER = "Informations à titre informatif, pas un conseil en investissement."
PAUSE_S = 0.9  # silence entre deux segments


# ---------------------------------------------------------------- épisodes
def load_episodes():
    eps = []
    for p in sorted(EPISODES_DIR.glob("*.json")):
        with p.open(encoding="utf-8") as f:
            d = json.load(f)
        d["id"] = p.stem
        if not d.get("segments"):
            print(f"! {p.name} : aucun segment, ignoré", file=sys.stderr)
            continue
        eps.append(d)
    eps.sort(key=pub_datetime, reverse=True)
    return eps


def pub_datetime(ep):
    """Date de publication : celle du brief, à 7h30 (quotidien) ou 9h (hebdo), heure de Paris.
    Si l'épisode a été créé plus tard (rattrapage), on prend creeLe."""
    day = dt.date.fromisoformat(ep["date"])
    hour, minute = (9, 0) if ep.get("type") == "hebdo" else (7, 30)
    base = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=PARIS)
    created = ep.get("creeLe")
    if created:
        try:
            c = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            if c.tzinfo and c > base and c.date() == day:
                return c
        except ValueError:
            pass
    return base


# ---------------------------------------------------------------- synthèse
async def _edge(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate=RATE).save(str(path))


def tts(text, path):
    """Synthèse d'un segment. Voix neuronale Microsoft (edge-tts), sinon gTTS."""
    try:
        asyncio.run(_edge(text, path))
        if path.exists() and path.stat().st_size > 1000:
            return "edge"
    except Exception as e:  # noqa: BLE001
        print(f"  edge-tts a échoué ({e}), essai gTTS", file=sys.stderr)
    from gtts import gTTS
    gTTS(text, lang="fr", tld="fr").save(str(path))
    return "gtts"


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def synth_episode(ep, out_dir):
    out = out_dir / f"{ep['id']}.mp3"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        silence = tmp / "silence.wav"
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
             "-t", str(PAUSE_S), silence])
        parts, engines = [], set()
        for i, seg in enumerate(ep["segments"]):
            text = (seg.get("texte") or "").strip()
            if not text:
                continue
            mp3 = tmp / f"seg{i:02d}.mp3"
            engines.add(tts(text, mp3))
            wav = tmp / f"seg{i:02d}.wav"
            run(["ffmpeg", "-y", "-i", mp3, "-ar", "24000", "-ac", "1", wav])
            if parts:
                parts.append(silence)
            parts.append(wav)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
             "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k",
             "-metadata", f"title={ep.get('titre', ep['id'])}",
             "-metadata", f"artist={SHOW_TITLE}",
             "-metadata", f"album={SHOW_TITLE}",
             "-metadata", f"date={ep['date']}",
             "-id3v2_version", "3", out])
    print(f"  {out.name} : {out.stat().st_size // 1024} Ko ({', '.join(sorted(engines))})")
    return out


# ---------------------------------------------------------------- flux RSS
def duration_s(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True, check=True)
    return int(float(r.stdout.strip()))


def hms(sec):
    return f"{sec // 3600:d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def episode_title(ep):
    d = dt.date.fromisoformat(ep["date"])
    if ep.get("type") == "hebdo":
        label = f"Récap S{d.isocalendar().week:02d}"
    else:
        label = d.strftime("%d/%m")
    return f"{label} · {ep.get('titre', '').strip()}"


def episode_desc(ep):
    titles = [s.get("titre", "") for s in ep["segments"] if s.get("titre")]
    lines = [ep.get("titre", ""), "", "Au sommaire :"]
    lines += [f"- {t}" for t in titles if t.lower() != "bonjour"]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def x(s):
    return html.escape(str(s), quote=True)


def write_feed(eps, site):
    now = email.utils.format_datetime(dt.datetime.now(dt.timezone.utc))
    items = []
    for ep in eps:
        mp3 = site / "audio" / f"{ep['id']}.mp3"
        if not mp3.exists():
            print(f"! MP3 manquant pour {ep['id']}, épisode hors flux", file=sys.stderr)
            continue
        url = f"{SITE_URL}/audio/{ep['id']}.mp3"
        desc = episode_desc(ep)
        ep["_url"], ep["_dur"] = url, duration_s(mp3)
        items.append(f"""    <item>
      <title>{x(episode_title(ep))}</title>
      <description>{x(desc)}</description>
      <itunes:summary>{x(desc)}</itunes:summary>
      <enclosure url="{x(url)}" length="{mp3.stat().st_size}" type="audio/mpeg"/>
      <guid isPermaLink="false">{x(ep['id'])}</guid>
      <pubDate>{email.utils.format_datetime(pub_datetime(ep))}</pubDate>
      <itunes:duration>{hms(ep['_dur'])}</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
      <itunes:explicit>false</itunes:explicit>
    </item>""")
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{x(SHOW_TITLE)}</title>
    <link>{x(SITE_URL)}/</link>
    <atom:link href="{x(SITE_URL)}/feed.xml" rel="self" type="application/rss+xml"/>
    <language>fr-fr</language>
    <description>{x(SHOW_DESC)}</description>
    <itunes:summary>{x(SHOW_DESC)}</itunes:summary>
    <itunes:author>Veille de Gaël</itunes:author>
    <itunes:image href="{x(SITE_URL)}/cover.png"/>
    <image><url>{x(SITE_URL)}/cover.png</url><title>{x(SHOW_TITLE)}</title><link>{x(SITE_URL)}/</link></image>
    <itunes:category text="Business"><itunes:category text="Investing"/></itunes:category>
    <itunes:explicit>false</itunes:explicit>
    <itunes:type>episodic</itunes:type>
    <itunes:block>Yes</itunes:block>
    <lastBuildDate>{now}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""
    (site / "feed.xml").write_text(feed, encoding="utf-8")
    return [e for e in eps if "_url" in e]


def write_index(eps, site):
    rows = "\n".join(
        f"""<article><h2>{x(episode_title(e))}</h2>
<p class="meta">{hms(e['_dur'])} · {'Récap hebdo' if e.get('type') == 'hebdo' else 'Brief du matin'}</p>
<audio controls preload="none" src="audio/{x(e['id'])}.mp3"></audio></article>"""
        for e in eps)
    page = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{x(SHOW_TITLE)}</title>
<link rel="alternate" type="application/rss+xml" title="{x(SHOW_TITLE)}" href="feed.xml">
<style>
body{{font-family:-apple-system,system-ui,sans-serif;max-width:640px;margin:0 auto;padding:24px 16px;color:#1c2430;background:#f6f4ef}}
header{{display:flex;gap:16px;align-items:center;margin-bottom:24px}}
header img{{width:88px;height:88px;border-radius:14px}}
h1{{font-size:1.4rem;margin:0}} h2{{font-size:1rem;margin:0 0 4px}}
.feed{{background:#fff;border-radius:12px;padding:12px 14px;font-size:.9rem;margin-bottom:24px}}
.feed code{{word-break:break-all}}
article{{background:#fff;border-radius:12px;padding:14px;margin-bottom:12px}}
.meta{{color:#6b7280;font-size:.85rem;margin:0 0 8px}} audio{{width:100%}}
footer{{color:#6b7280;font-size:.8rem;margin-top:24px}}
</style></head><body>
<header><img src="cover.png" alt=""><div><h1>{x(SHOW_TITLE)}</h1><p class="meta">{x(SHOW_DESC)}</p></div></header>
<div class="feed">S'abonner dans Podcasts (iPhone) : Bibliothèque → ••• → « Suivre une émission par URL », puis coller<br>
<code>{x(SITE_URL)}/feed.xml</code></div>
{rows}
<footer>{x(DISCLAIMER)}</footer>
</body></html>
"""
    (site / "index.html").write_text(page, encoding="utf-8")


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("missing"); m.add_argument("--existing", required=True)
    s = sub.add_parser("synth"); s.add_argument("ids", nargs="+"); s.add_argument("--out", required=True)
    k = sub.add_parser("keep"); k.add_argument("--keep", type=int, default=40)
    w = sub.add_parser("site"); w.add_argument("--site", required=True); w.add_argument("--keep", type=int, default=40)
    a = ap.parse_args()

    eps = load_episodes()
    if a.cmd == "missing":
        have = set(pathlib.Path(a.existing).read_text().split())
        for e in eps:
            if f"{e['id']}.mp3" not in have:
                print(e["id"])
    elif a.cmd == "synth":
        out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
        by_id = {e["id"]: e for e in eps}
        for i in a.ids:
            print(f"Synthèse {i} (voix {VOICE})")
            synth_episode(by_id[i], out)
    elif a.cmd == "keep":
        for e in eps[: a.keep]:
            print(e["id"])
    elif a.cmd == "site":
        site = pathlib.Path(a.site); (site / "audio").mkdir(parents=True, exist_ok=True)
        for f in STATIC_DIR.iterdir():
            shutil.copy(f, site / f.name)
        listed = write_feed(eps[: a.keep], site)
        write_index(listed, site)
        (site / ".nojekyll").write_text("")
        print(f"Flux : {len(listed)} épisode(s) → {SITE_URL}/feed.xml")


if __name__ == "__main__":
    main()
