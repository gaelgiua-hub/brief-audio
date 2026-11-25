# HANDOFF — brief-audio

Podcast privé (non référencé) qui lit à voix haute la veille boursière de Gaël,
écoutable dans l'app **Podcasts** de son iPhone.

- Flux : https://gaelgiua-hub.github.io/brief-audio/feed.xml
- Page web (lecteurs audio) : https://gaelgiua-hub.github.io/brief-audio/
- Abonnement iPhone : Podcasts → Bibliothèque → ••• → « Suivre une émission par URL » → coller le flux.

## Chaîne complète

1. **Veille 1 - Collecte** (6h15 lun-ven) → Sheet « Collecte » (Drive).
2. **Veille 2 - Analyse et brief** (7h02 lun-ven) → Doc « AAAA-MM-JJ - Brief marchés » (dossier Briefs).
3. **Veille 3 - Compilation hebdo** (sam 8h31) → Doc « AAAA-Sxx - Récap hebdo marchés ».
4. **Veille 5 - Brief audio** (7h24 lun-ven) et **Veille 5b - Brief audio hebdo** (sam 8h53) :
   écrivent le script parlé de l'épisode (JSON) et le déposent à deux endroits :
   - la base de la page claude.ai « Brief audio de Gaël » (lecture par la voix du navigateur) ;
   - **ce dépôt**, dans `episodes/<id>.json` (commit + push sur `main`).
5. **GitHub Actions** (`.github/workflows/podcast.yml`), déclenché par le push :
   - fabrique le MP3 des nouveaux épisodes (`scripts/build.py synth`) avec edge-tts
     (voix `fr-FR-HenriNeural`, repli gTTS si edge-tts échoue), pauses entre segments,
     normalisation du volume, MP3 mono 64 kb/s ;
   - range les MP3 dans la release GitHub `audio` (stockage hors historique git) ;
   - reconstruit `feed.xml` + `index.html` avec les 40 épisodes les plus récents
     et publie sur GitHub Pages.

## Format d'un épisode (`episodes/<id>.json`)

`<id>` = `AAAA-MM-JJ-quotidien` ou `AAAA-Sxx-hebdo`.

```json
{
  "date": "AAAA-MM-JJ",
  "type": "quotidien | hebdo",
  "titre": "Titre court",
  "dureeMin": 5,
  "source": {"titre": "...", "url": "https://docs.google.com/..."},
  "creeLe": "ISO 8601",
  "segments": [{"titre": "Bonjour", "texte": "..."}]
}
```

Le texte est lu tel quel : pas d'URL, pas de Markdown, abréviations écrites en toutes lettres.
Un épisode déjà publié n'est pas re-synthétisé (le MP3 existe dans la release).
Pour le refaire : Actions → « Construire le podcast » → Run workflow, en indiquant l'identifiant dans « refaire »
(ou supprimer l'asset `<id>.mp3` de la release `audio`, puis relancer le workflow).

## Réglages

Dans `podcast.yml` (bloc `env`) : `TTS_VOICE` (actuelle : `fr-FR-VivienneMultilingualNeural` ; autres : `fr-FR-DeniseNeural`, `fr-FR-HenriNeural`),
`KEEP` (épisodes gardés dans le flux). Pochette : `static/cover.png`.

## Points d'attention

- Le dépôt et le site sont **publics** (obligatoire pour GitHub Pages gratuit et pour l'app Podcasts).
  Le flux porte `itunes:block` et la page `noindex`, mais quiconque a l'URL peut écouter.
  Ne jamais mettre de donnée client ou personnelle dans un épisode.
- edge-tts utilise le service de lecture de Microsoft Edge, gratuit mais non officiel : s'il casse,
  le repli gTTS prend le relais (voix moins naturelle). Le journal du workflow indique le moteur utilisé.
- Lancer à la main : onglet Actions → « Construire le podcast » → Run workflow.
