Feature 12 — real SFX audio clip folder
========================================

Drop your short sound-effect clips here and they start working immediately
(config/config is read at startup; restart once after adding files).

Naming:  <stem><optional digits>.<extension>
  accepted extensions: .wav  .ogg  .mp3  .flac

Default stems used by config/sfx.yaml:
  heart     <- sentence contains ♥   (deep pleasure / affection)
  tender    <- sentence contains ♡   (soft, tender warmth)
  giggle    <- sentence contains ♪   (playful, sing-song)
  breath    <- sentence contains ~   (blissful, elongated sound)
  kiss      <- onomatopoeia like "mwah" / "smooch"
  suck      <- onomatopoeia like "slurp" / "schlick"
  gasp      <- onomatopoeia like "gasp" / "huff"
  moan      <- "mmm" / "mmh" / "mmph"
  whimper   <- "hnnng" / "nngh" / "nnh"
  sigh      <- "sigh" / "haaah" / "aahh"
  laugh     <- "hehehe" / "haha" / "teehee"
  purr      <- "prrr" / "mrrr" / "mrrp"
  heartbeat <- "ba-dum" / "thump-thump"
  sniff     <- "sniff" / "snff"
  gulp      <- "gulp" / "glp"

Examples:  breath_1.wav  breath_2.wav  giggle1.ogg  kiss_01.mp3

Feature 12c (default): the matched word/symbol is removed from the text TTS
speaks and the clip plays AT THAT SPOT, so "mwah" is never read aloud as a
word.  A word whose clip is missing is spoken normally instead - nothing
is ever silently dropped.  Set splice: false in config/sfx.yaml for the old
sentence-start behaviour.

Where the shipped clips come from:
  heart / tender / giggle / moan / whimper / sigh / laugh / purr
                                her own voice        (tools/gen_sfx.py)
  kiss / breath / gasp / suck / heartbeat / sniff / gulp
                                procedural foley     (tools/gen_sfx_foley.py)
Up to three takes per stem are shipped; the player picks one at random.
Drop your own recordings over these names any time.

Rules the player follows:
  * at most "max_per_sentence" cues per sentence (default 4)
  * up to three takes per stem: the player picks one at random
  * same category not repeated within "cooldown" seconds (default 2 s)
  * never two clips within "gap" seconds of each other (default 0.4 s)
  * a spliced cue plays where its word was, then a short beat
    (cue_beat_s, default 0.25 s) before the next words resume
  * the bubble still shows the word/marker; the clip stops when a turn is
    interrupted
  * missing clips / wrong names / disabled config = completely silent
-------
Clips are generated locally by the tools above (her Fish voice or numpy
foley); drop-ins are yours.  Keep them legal and the persona
adult-consensual (Eve is 34).
