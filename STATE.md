# CryptoPairsBot: κατάσταση project (handoff)

> Τελευταία ενημέρωση: **2026-10-01, ~23:00 ώρα Ελλάδας**. Γράφτηκε για να συνεχίσει ένα νέο session από εδώ.
> Γλώσσα επικοινωνίας με τον χρήστη: **ελληνικά** (κώδικας και τεχνικοί όροι στα αγγλικά).

---

## 0. TL;DR

- **Στόχος:** bot για pairs trading σε Binance USDⓈ-M perpetuals, βασισμένο στο paper των Stoikov et al., *Pairs Trading in Crypto* (`ssrn-6188418.pdf`). Τρέχει πρώτα σε **paper trading** και μετά σε **live**.
- **Τι υπάρχει:** πλήρες σύστημα σε Python, χτισμένο από την αρχή πάνω στον αρχικό κώδικα (`CryptoPairTrading-main.zip`, φυλάσσεται στο `legacy/`):
  - έρευνα: λήψη δεδομένων, funnel, βαθμονόμηση, backtest με ρεαλιστικά κόστη, πειράματα validation/test;
  - paper bot: Kalman filter, εκτέλεση «maker πρώτα», κίνδυνος, βάση SQLite με ανάκτηση μετά από crash;
  - real-time dashboard **πολλών σελίδων** (σκούρο θέμα, what-if sliders, Backtest lab, εφαρμογή παραμέτρων στο bot);
  - Docker για VPS: bot + dashboard, `deploy.sh`, οδηγός `DEPLOY.md` (το build δεν έχει δοκιμαστεί);
  - **111 tests περνούν**.
- **Βασικό εύρημα:** με ρεαλιστικά κόστη η στρατηγική έχει **μικρό και ασταθές πλεονέκτημα**. Η καλύτερη εκδοχή (K1 Kalman) έβγαλε **−9,7% στο validation** (Αύγ. 2025 – Φεβ. 2026) και **+27,5% στο test** (Μάρ. – Σεπ. 2026). **Δεν έχει προταθεί live με πραγματικά χρήματα.** Το paper trading είναι η τρέχουσα «αληθινή» δοκιμή.
- **Τι τρέχει τώρα (τοπικά):**
  - paper bot με profile `kalman` (K1), 3 ανοιχτές θέσεις, equity ≈ €1.700;
  - dashboard στο http://localhost:8501.
  - Και τα δύο επανεκκινήθηκαν στις 20:51–20:52 (χωρίς `--fresh`) με το schema v3. Οι θέσεις διατηρήθηκαν.
  - **Το νέο dashboard (v3, σελίδες) γράφτηκε μετά.** Θέλει ξανά επανεκκίνηση και των δύο, χωρίς `--fresh`. Δες §1 #0.

---

> **2026-10-01 23:54 (ώρα Ελλάδας): το paper bot ΤΡΕΧΕΙ ΣΤΟΝ SERVER**: OVHcloud VPS-1, `152.228.175.62` (`vps-3ce1c275.vps.ovh.net`), Γαλλία, Ubuntu 24.04, Docker 29.8 / Compose v5.5. Η βάση μεταφέρθηκε από το PC: ίδιες 4 θέσεις, νέα συνεδρία `paper_20261001_205409_394c0f` στην ίδια `paper.db`. Κενά: 23:18–23:20 και ~1 λεπτό στις 23:54. **Το τοπικό bot και dashboard είναι ΣΤΑΜΑΤΗΜΕΝΑ. Μην τα ξεκινήσεις τοπικά** (η τοπική `state/paper.db` είναι πλέον παλιό αντίγραφο). SSH: `ssh cryptobot` (χρήστης `ubuntu`, μέλος του group `docker`, sudo χωρίς κωδικό). Η σύνδεση με κωδικό είναι κλειστή (`/etc/ssh/sshd_config.d/00-cryptobot-keys-only.conf`). Τον κωδικό του `ubuntu` τον όρισε ο χρήστης και τον ξέρει μόνο εκείνος. Το `data/` έχει ανέβει (Backtest lab). Dashboard: `ssh -N -L 8502:127.0.0.1:8501 cryptobot` → http://localhost:8502. Ενημερώσεις: `./deploy.sh`.

> **2026-10-02 (~12:30): το dashboard ανοίγει και ως εφαρμογή μέσω Tailscale** (`DEPLOY.md` §12). Λογαριασμός Tailscale του χρήστη, tailnet `tail5610d8.ts.net`. Server: `cryptobot` (100.88.209.86), `tailscale up --hostname=cryptobot --accept-dns=false`, ώστε να μην αλλάξει το DNS του bot. `tailscale serve --bg http://127.0.0.1:8501` → **https://cryptobot.tail5610d8.ts.net**, μόνο μέσα στο tailnet, όχι Funnel. Το docker-compose δεν άλλαξε, και το dashboard μένει στο `127.0.0.1:8501`. Το PC του χρήστη (Linux Mint 22.3, XFCE, μόνο Firefox) είναι στο tailnet ως `rigojohn-ms-7922`. Εφαρμογή «CryptoPairs Desk»: φτιάχτηκε με το Web Apps του Mint (Firefox, απομονωμένο προφίλ), με launcher `~/.local/share/applications/WebApp-CryptoPairsDesk3558.desktop` και αντίγραφο στο `~/Desktop` με checksum εμπιστοσύνης του XFCE. Εικονίδιο: `~/.local/share/ice/icons/cryptopairs.{svg,png}`. Επαληθεύτηκε σε headless Firefox μέσω του tailnet: health `ok`, websocket ΟΚ, live δεδομένα, κανένα σφάλμα. Επόμενα βήματα για τον χρήστη: λάπτοπ Windows (Tailscale + Edge «Install this site as an app») και **Disable key expiry** για τον `cryptobot` στο admin console (αλλιώς λήγει σε 180 μέρες). Για να μπει το Tailscale στο PC χρειάστηκε το νέο κλειδί apt του Spotify (`5384CE82BA52C83A`).

> **2026-10-02 (~14:00): dashboard σε επίπεδο fund, Φάση 1 «Pair desk» ΓΡΑΦΤΗΚΕ, ΔΕΝ ΕΧΕΙ ΓΙΝΕΙ DEPLOY.** Ο server τρέχει ακόμα τον παλιό κώδικα (βάση v3). Για ενεργοποίηση: `./deploy.sh` (ξαναχτίζει το image και κάνει restart **και στα δύο** containers, χωρίς `--fresh`). Οι θέσεις διατηρούνται και η βάση περνά αυτόματα σε **schema v4**. Μετά: ένα δοκιμαστικό χειροκίνητο άνοιγμα και κλείσιμο 50% / 100% σε ένα ζεύγος και έλεγχος του `done` στο Command history. Σχέδιο: `~/.claude/plans/lazy-inventing-crab.md`. Συμφωνήθηκε: Φάση 2 = Overview σε στυλ fund, Φάση 3 = οι υπόλοιπες σελίδες, μία ανά παράδοση. Τι άλλαξε:
> - **Γραφήματα σε TradingView Lightweight Charts 5.2.1:** vendored στο `ui/vendor/` και αντιγραμμένο στο `static/` στην εκκίνηση (`tv.ensure_static_files`). Όψεις: Spread Z (1m/5m/15m από τα 5 s του bot των τελευταίων 24 h, ή 1h από το `fit_history`), Legs (κεριά Binance με όγκο), Relative, Model. Αρχεία: `ui/tv.py`, `ui/tvcharts.py`, `ui/js/{common,chart,market}.js`.
> - **Αγορά real time στον browser** (όχι μέσω bot): orderbook 10 επιπέδων ανά σκέλος, tape, mark/index, funding με αντίστροφη μέτρηση, live Z και uPnL.
> - **Προφίλ ζεύγους:** όνομα και market cap από CoinGecko, sector από Binance, blockchains, OI, funding, tick/lot. Στατιστικά (`research/pairstats.py`: συσχέτιση, β OLS, half-life, Hurst, ADF Engle-Granger), κυλιόμενη συσχέτιση, ιστορικό βαθμονόμησης (`runs/<profile>/test_pairs.csv`, το dashboard έχει πλέον mount `./runs`) έναντι του paper.
> - **Order ticket** (`ui/ticket.py` preview από το πραγματικό βιβλίο, `ui/desk.py`): Open long/short με leverage ≤ `risk.manual_max_leverage` (null = strategy.leverage) και Market ή Maker-first. Close 25/50/75/100% Market ή Maker. **Exit plan** ανά θέση (exit Z, stop-loss %), ενώ το catastrophe stop μένει σκληρό όριο. Χωρίς βιβλίο στο dashboard η εντολή στέλνεται κανονικά (το bot τιμολογεί με τις δικές του τιμές).
> - **Bot:**
>   - νέες εντολές `open_pair` και `set_exit_plan`; το `close_pair` δέχεται πλέον `fraction` και `execution`;
>   - `engine.open_pair/close_pair/set_exit_plan`; order kind `reduce` (`PairBook.reduce`, `store.commit_reduce`, event `position_reduced`);
>   - οι maker εντολές εκτελούνται στο παρασκήνιο (`_spawn`) και ακυρώνονται στο shutdown;
>   - `PairStrategy.z_exit_override/stop_pct_override` (στο snapshot; με `None` δεν αλλάζει τίποτα, άρα strict = paper);
>   - `trades.entry_reason` (`signal`/`manual`); η μετάβαση v3→v4 είναι idempotent.
> - **103 tests περνούν.** Επαληθεύτηκε σε headless Firefox πάνω σε read-only αντίγραφο της βάσης του server (θύρα 8599): όλες οι όψεις, βιβλία, tape, ticket, προφίλ, χωρίς σφάλματα.

> **2026-10-02 (~16:30): audit της στρατηγικής (χωρίς αλλαγές κώδικα).** Επεξήγηση για τον χρήστη: `docs/strategy_explained.pdf`.
> - **Μαθηματικά και κώδικας σωστά:** Z, Kalman (prior, χωρίς lookahead), bid/ask Z, sizing 1/(1+β), φίλτρο κόστους (30 bps round trip, ίδιες μονάδες με το edge), πρόσημο funding. Η λογιστική του server επαληθεύτηκε στο λεπτό του ευρώ (BAND/GTC −23,91 = σκέλη − fees). Οι αλλαγές της Φάσης 1 (Pair desk) αφήνουν τα μαθηματικά αμετάβλητα (overrides = None).
> - **Bot ≠ backtest:** το bot τρέχει το Kalman στις τελευταίες 8.760 h (`kalman_history`), ενώ το backtest/βαθμονόμηση σε όλο το archive. Το β εξαρτάται από την αρχή (MAGIC/CTSI 0,99 με 1 έτος, 0,56 με 6 μήνες· SFP/SSV 0,57 έναντι 0,42 στο full). Το backtest μπήκε ALICE/T SHORT (+€17), το bot όχι. **Διόρθωση που εκκρεμεί:** το bot να φορτώνει το archive `data/` + REST για τις υπόλοιπες ώρες.
> - **Το validated K1 ≠ live profile:** τα `runs/experiments_kalman_v2` έτρεξαν με base `hardened` + kalman: χωρίς `max_entry_z` (MeanAbsZEntry 167.752 στο validation = trades σε παγωμένες τιμές) και χωρίς `maker_legs`. Νέες συγκρίσεις πρέπει να τρέχουν με το ακριβές profile `kalman`.
> - **Υπερπροσαρμογή βαθμονόμησης:** corr(train Sharpe, OOS Sharpe) ανά ζεύγος = **0,05** (validation) / **0,09** (test). In-sample μέση απόδοση ζεύγους **+653%** έναντι −9,7% / +27,5% OOS. OOS διάμεσος ζεύγους −23,1% (validation, 35% θετικά) / +2,5% (test, 53% θετικά): το +27,5% το κάνουν λίγα ζεύγη (HFT/ARB +376%, BAND/OGN +321%).
> - **Ουρές:** in-sample μέσο κέρδος +11,5%, μέση ζημιά −23,4% (stops 12,7%), άρα breakeven win rate ≈ 67%. In-sample 87%, OOS validation ~65%. Χωρίς τις 10–11/10/2025 το validation K1 θα ήταν +11% (RetExCrash).
> - **Ουδετερότητα αγοράς ΟΚ:** μέσο |β προς BTC| της θέσης με Kalman β = 0,16 (με β από αποδόσεις θα ήταν 0,53: attenuation). Εξαιρέσεις: SFP/ROSE 0,33, SFP/SSV 0,26 (διαφορετικά β των σκελών). Annual vol θέσης ανά μονάδα notional ~58%.
> - **Φίλτρο άλματος: ΑΠΟΡΡΙΠΤΕΤΑΙ.** In-sample (validation-stage ζεύγη, 2023-08..2025-07, 3.081 trades) οι είσοδοι μετά από άλμα >6σ ενός σκέλους έχουν μέση απόδοση +10,4% έναντι +5,8% για ήσυχες (<2σ), με λίγο περισσότερα stops (16,6% έναντι 12,4%). Η υπεραντίδραση είναι μέρος του edge. Δεν κοιτάχτηκε OOS.
> - **Προτεινόμενο πλάνο:** (1) διόρθωση ιστορικού Kalman· (2) walk-forward με πολλά folds αντί για 2 παράθυρα των 7 μηνών· (3) προκαθορισμένες εκδοχές: κοινές παράμετροι για όλα τα ζεύγη, sizing ίσου ρίσκου (∝ 1/vol spread), φίλτρο half-life/ADF στο funnel· (4) το paper συνεχίζει χωρίς αλλαγές. Συγκέντρωση στα live ζεύγη: BAND και OGN σε 4 ζεύγη το καθένα, SFP και CTSI σε 3.

> **2026-10-02 (~17:40): Φάση 2 (Overview σε στυλ fund) + ώρα Αθήνας + νέο sidebar, ΓΡΑΦΤΗΚΑΝ, ΔΕΝ ΕΧΕΙ ΓΙΝΕΙ DEPLOY** (μαζί με τη Φάση 1: ένα `./deploy.sh` τα ανεβάζει όλα).
> - **Overview** (`ui/views/overview.py`, υπολογισμοί στο `ui/fund.py`):
>   - 16 KPI: NAV, Today, MTD, από την αρχή, Sharpe/Sortino, vol, Max DD, kill switch, gross/net έκθεση, BTC-equivalent (beta 30 ημερών ανά νόμισμα), funding carry/ημέρα, ανοιχτές θέσεις, hit rate, profit factor, μέση διάρκεια;
>   - γράφημα NAV TradingView (Intraday 1' / Hourly, baseline στο κεφάλαιο, drawdown);
>   - πίνακας αποδόσεων ανά περίοδο και waterfall κεφάλαιο → spread → fees → funding → NAV;
>   - **blotter** με επιλογή γραμμής και κουμπιά: Pair desk / Close 50% / Close 100% / Pause;
>   - **προειδοποίηση funding** όταν μια θέση πληρώνει ≥ 0,5% του notional ανά ημέρα;
>   - attribution ανά ζεύγος, ημερολόγιο ημερήσιου PnL, signal radar, συσχέτιση PnL ζευγών.
> - **Ώρα Αθήνας παντού** (`ui/data.py`: `TZ`, `to_time`, `clock`, `day_start_ms`, και `CRYPTOPAIRS_TZ` για αλλαγή):
>   - πίνακες, Plotly, άξονες και crosshair του TradingView (Intl, με θερινή ώρα), tape;
>   - «Today» = από τα μεσάνυχτα Αθήνας, με την ίδια βάση (`fund.value_before`) σε strip, KPI και ημερολόγιο;
>   - το log του bot γράφεται πλέον πάντα σε UTC (`cli`, `Formatter.converter = gmtime`), και το System logs το δείχνει σε ώρα Αθήνας;
>   - το `tzdata` μπήκε στα requirements.
> - **Sidebar:**
>   - επιλογή bot, μενού «View · pair · refresh» (focus, History, Live update, Pairs in view);
>   - **live watchlist** (`ui/js/watchlist.js`): ανοιχτές θέσεις πρώτα, μετά τα flat κατά απόσταση από σήμα, και ένα κλικ = focus σε όλες τις σελίδες (`_focus_pick`);
>   - η φόρμα **what-if** μεταφέρθηκε στο Pair desk (Strategy parameters) και στο Backtest lab (expander).
> - **111 tests περνούν.** Ελέγχθηκε σε headless Firefox πάνω σε read-only αντίγραφο της βάσης του server (κλικ στη watchlist, ώρες, όλες οι σελίδες χωρίς σφάλματα).
> - **Εύρημα (17:30):** το YFI/SAND (LONG, short SAND) πληρώνει funding **−1,46% ανά 8 h στο SAND**, δηλαδή ~3,1 USDT/ημέρα, ~1,5% του notional ανά ημέρα, ενώ ήταν ήδη −7,96 σε uPnL. Το GTC είχε −0,89% όταν σταμάτησε το BAND/GTC (−23,91). Η στρατηγική δεν τιμολογεί το funding στις εξόδους (`funding_filter` off). Η απόφαση ανήκει στον χρήστη.

> **2026-10-02 (~17:55): ΣΕ ΛΕΙΤΟΥΡΓΙΑ στον server η Φάση 1 + η Φάση 2.** Το dashboard έπεσε για ~10 λεπτά στις 17:46:
> - Αιτία: ένα **rewind με επαναφορά κώδικα** (17:44:50) γύρισε πίσω μόνο τα αρχεία που είχαν γραφτεί με Write/Edit και έσβησε το `ui/fund.py`. Τα αρχεία που είχαν αλλάξει με scripts στο Bash (`widgets.py`, `data.py`, `charts.py` κ.ά.) έμειναν στη Φάση 2, που ζητούσε το `fund`. Αυτός ο μισός κώδικας ανέβηκε με το `./deploy.sh` στις 17:46, και όλες οι σελίδες έβγαζαν `ImportError: cannot import name 'fund'`.
> - Διόρθωση: τα 12 αρχεία επανήλθαν από το `~/.claude/file-history/5e72b61e…/`, snapshot 17:38:32 (τέλος της Φάσης 2). Το αντίγραφο της χαλασμένης κατάστασης είναι στο scratchpad (`before-restore/tree.tgz`). 111 tests περνούν.
> - Έγινε rebuild **μόνο του dashboard** στις ~17:53. Το bot (image 17:46, ίδιος κώδικας bot) δεν έκανε restart: 4 θέσεις, schema v4. Επαληθεύτηκε μέσω https://cryptobot.tail5610d8.ts.net (Overview, Pair desk, χωρίς σφάλματα).

> **2026-10-02 (~18:30): νέο γράφημα NAV + drawdown στο Overview, ΓΡΑΦΤΗΚΕ, ΔΕΝ ΕΧΕΙ ΓΙΝΕΙ DEPLOY** (ο χρήστης: «δεν μου αρέσει καθόλου το Overview, θέλω καλύτερα το chart equity και max drawdown»).
> - Στην κορυφή, σε όλο το πλάτος: γραμμή με NAV, αλλαγή διαστήματος, high-water mark, drawdown τώρα / ώρες κάτω από το νερό, max drawdown και ποσοστό ανάκτησης. Από κάτω το γράφημα 1H / 6H / 1D / 1W / 1M / ALL (`fund.RANGES`, ~700 σημεία το καθένα): NAV με baseline στην αρχή του διαστήματος, dotted high-water mark (`role="guide"`, δεν επηρεάζει την κλίμακα), κλεισίματα trades με το PnL τους, και underwater drawdown με το ακριβές max (`fund.range_max_drawdown`). Ακολουθούν οι πίνακες Drawdowns (`fund.drawdown_episodes`), Returns και ο waterfall. Οι 16 κάρτες είναι πλέον πιο κάτω («Risk & book»).
> - Κενά = το bot δεν έτρεχε (`fund.nav_grid`: ανοχή 90 s για τα live σημεία, 2 h για τις ωριαίες τιμές).
> - **Bot:** το `equity_live` δεν σβήνεται πια στις 24 h. Μετά τις 24 h αραιώνεται σε 1 σημείο/λεπτό και κρατιέται `runtime.equity_history_days: 400` (`store.prune_live(..., equity_older_than_ms)`). Το `pair_live` μένει στις 24 h. **Χρειάζεται `./deploy.sh` πριν από τις ~20:46 ώρα Αθήνας της 2026-10-02**, αλλιώς χάνεται η πρώτη ώρα του ιστορικού των 5 s.
> - 113 tests περνούν. Επαληθεύτηκε σε headless Firefox (1H / 6H / 1D / ALL, χωρίς σφάλματα).
> - Από την οθόνη: το MAGIC/CTSI ήταν −17,7%, **2,2% από το stop-loss** (20%).

> **2026-10-02 (~19:00): το γράφημα NAV δείχνει πλέον σε κάθε timeframe από την αρχή του paper. ΣΕ ΛΕΙΤΟΥΡΓΙΑ** (rebuild μόνο του dashboard· το bot και η διατήρηση του equity ανέβηκαν με το `./deploy.sh` του χρήστη, ~18:34).
> - Τα κουμπιά είναι timeframes, όπως στο TradingView: `fund.TIMEFRAMES` = 1m / 5m / 15m / 1H / 4H / 1D, δηλαδή πόσο χρόνο καλύπτει κάθε σημείο. Το γράφημα πηγαίνει πάντα από την έναρξη της πρώτης συνεδρίας (01/10 19:50) ως τώρα, και η προεπιλογή είναι το πιο λεπτό timeframe με ≤ 1.500 σημεία (`fund.default_timeframe`). Πάνω από 50.000 σημεία γίνεται αραίωση.
> - Τα 4H/1D κλείνουν σε ώρες Αθήνας (`nav_grid(offset_ms)`). Το HWM και το drawdown κάθε bar (το χειρότερο σημείο μέσα στο bar) υπολογίζονται από όλα τα στιγμιότυπα, οπότε είναι ίδια σε κάθε timeframe.
> - Η όψη κρατά όλο το ιστορικό καθώς προστίθενται σημεία (`spec.fit`), μέχρι να κάνει ο χρήστης zoom ή pan. Με διπλό κλικ ξαναδείχνει όλο το ιστορικό.
> - 114 tests περνούν.

> **2026-10-05: δοκιμαστικό bot «B» με τις διορθώσεις του audit, δίπλα στο «A». ΣΕ ΛΕΙΤΟΥΡΓΙΑ στον server από τις 16:03 UTC (19:03 ώρα Ελλάδας).** Σχέδιο: `~/.claude/plans/serialized-chasing-sparkle.md`. Ο χρήστης: «κάν' το δοκιμαστικά, κι αν δουλέψει καλύτερα το κρατάμε».
> - **Deploy:** rsync όπως στο `deploy.sh`, και μετά `docker compose up -d --build bot_b`, **μόνο το B**. Το A (`bot`) και το dashboard δεν έκαναν restart: τρέχουν ακόμα το image της 2/10, και το A παραμένει «Up 3 days». Ένα επόμενο `./deploy.sh` θα ξαναχτίσει και το A με τον νέο κώδικα (`kalman_history: 8760`, άρα ίδια συμπεριφορά) και θα του κάνει restart χωρίς `--fresh`.
> - Συνεδρία B: `paper_20261005_160337_2cb4b3`, 19 ζεύγη (το OGN/LRC παραλείφθηκε), €1.900. Ανά νόμισμα: 29.232 bars από το archive + 112 REST. Πρώτες θέσεις: OGN/GRT LONG, OGN/ANKR SHORT, ARPA/ASTR SHORT (maker 52–61%).
> - **Επαληθεύτηκε:** β/σ του OGN/GRT στο B = 0,742 / 0,1374, ίδια με το `estimate_fits` στο PC (archive + Binance ως 15:00 UTC). Πόροι: B ~330 MB, ~20% CPU.
> - **2026-10-05 18:18 UTC: instant exit στο B** (ο χρήστης: «από εδώ και πέρα να βγαίνει επιτόπου με το που χτυπήσει το exit»· διάλεξε «κατευθείαν στο B» αντί για ξεχωριστό bot C).
>   - `runtime.realtime_exits: true` στο `kalman_b`: κάθε 5 s, `engine.check_exits_live` εφαρμόζει τον ίδιο κανόνα εξόδου στο live Z (bid/ask, παγωμένο fit), με `PairStrategy.check_exit(..., check_stop=False)`. Το stop 20% και το time stop μένουν στο κλείσιμο της ώρας· το stop 35% μένει στο `check_stops`.
>   - Κλείσιμο maker-first στο παρασκήνιο (`_spawn(_run)`), όπως οι ωριαίες έξοδοι. Μόνο με φρέσκες τιμές. `PairRuntime.exit_bar`: καμία είσοδος στο ίδιο bar. Event `realtime_exit`.
>   - Deploy μόνο του `bot_b`: 4 θέσεις επανήλθαν, «realtime exits: on». Το A δεν άλλαξε.
>   - **Προσοχή στη σύγκριση A/B:** το B διαφέρει πλέον από το A και στις εξόδους. Το backtest μένει ωριαίο, άρα για το instant exit δεν υπάρχει έλεγχος σε backtest.
>   - 133 tests περνούν (`tests/test_realtime_exits.py`).
> - **A** = το σημερινό bot (`bot`, `state/paper.db`, profile `kalman`), **χωρίς καμία αλλαγή**.
> - **B** = νέο service `bot_b` (`state/kalman_b.db`, `logs/kalman_b.log`, profile `kalman_b`, `runs/kalman_b/pairs.yaml`). Διαφορές από το A:
>   - `strategy.kalman_history: null`: το bot ξεκινά από το archive `data/klines/1h` και συνεχίζει με REST, όπως το backtest (`runtime/app.py` `archive_loader`, `feed.DataFeed(archive=, capacity=)`, `engine._kalman` με cache ανά ζεύγος, `_kalman_bars`). Mount `./data:/app/data:ro`.
>   - **Φρέσκα ζεύγη:** `python -m cryptopairs --profile kalman_b research --final --tag kalman_b --top 20` (νέο `cli.cmd_final`): funnel και βαθμονόμηση σε όλα τα δεδομένα ως 30/9/2026, χωρίς test παράθυρο, κατάταξη κατά IC. Αποτέλεσμα στο `runs/kalman_b/final.json`.
>   - `risk.max_symbol_gross_leverage: 0.15` αντί για 2,0. Το 0,3 του σχεδίου δεν θα έπιανε ποτέ (BAND ×4 ≈ €500 < 30% του €2.000), ενώ το 0,15 ≈ 2 σκέλη ανά νόμισμα.
> - **Backtest A έναντι B:** νέα εκδοχή `"K1-1y bot refits on its last year"` (`research.score_kalman_history: 8760`, `core/signals.kalman_filter_windowed`, επανεκκίνηση ανά 168 h ≈ το bot A, με β MAGIC/CTSI 0,990 = του A). Μοιράζεται τη βαθμονόμηση του K1. Εντολή (ο χρήστης, ~30'): `python -m cryptopairs --profile kalman experiments --only K1 --out runs/experiments_ab`. Τρέχει και το «K1 Kalman filter» με το **ακριβές** profile του bot, που διορθώνει το λάθος #2 του audit.
> - **Κανόνας (γράφτηκε πριν):** το B αντικαθιστά το A αν (1) στο validation K1 ≥ K1-1y και (2) μετά από 3 εβδομάδες paper το B δεν έχει λάθη ή απρόσμενα κόστη και δεν πάει χειρότερα από το A. Αλλιώς `docker compose stop bot_b` και αφαίρεση του service.
> - `deploy.sh` ανεβάζει και το `runs/kalman_b`. 119 tests περνούν (`tests/test_kalman_history.py`: windowed = full όταν το παράθυρο καλύπτει τη σειρά· windowed = bot στις επανεκκινήσεις· feed από archive + REST· β bot = β backtest· `research --final`).

> **2026-10-05 (~19:30): Pair desk, ένα Z σε όλα τα timeframes. ΣΕ ΛΕΙΤΟΥΡΓΙΑ** (20:34 ώρα Αθήνας: rsync + `docker compose build dashboard && docker compose up -d --no-deps dashboard`· τα bot A (από 02/10) και B (από 05/10 19:03) δεν έκαναν restart. Επαληθεύτηκε μέσω https://cryptobot.tail5610d8.ts.net/pair, χωρίς σφάλματα). Σημείωση: το **bot B τρέχει ήδη στον server** (`cryptopairs-b`, από 05/10 19:03 Αθήνας), το έβαλε μπροστά ο χρήστης. Ο χρήστης: «έχεις μπερδέψει το model Z, το Z strategy και το Z Binance».
> - **Spread Z** (`tvcharts.z_spec`): μία γραμμή Z = τιμές Binance (κλείσιμο κάθε bar) με το fit που ίσχυε εκείνη τη στιγμή (`fits_in_force`: `used_*`, αλλιώς το fit της θέσης από το `trades`, αλλιώς το Kalman της ώρας). Timeframes 1m/5m/15m/1h/4h, κάθε σημείο στην ώρα κλεισίματος, άρα ίδιο Z την ίδια στιγμή σε όλα. Το τελευταίο σημείο κινείται live (bookTicker, ίδιο με το ticker). Κάτω pane: spread σε % με ζώνες Z × σ. Τα markers των ωριαίων αποφάσεων μπαίνουν στο πρώτο intraday bar μετά την ώρα.
> - Το model Z πήγε στην όψη **Model** («Z if refit now» έναντι «Z in use»). Το **Relative** δείχνει μόνο τιμές (έφυγε το «Z · today's fit»). Το `load_pair` δεν φορτώνει πλέον `pair_live`.
> - **Λάθη που βρέθηκαν:** (1) σε κάθε restart το bot ξαναγράφει 720 ώρες `fit_history` με `used_* = NULL` (`app.py` backfill, `INSERT OR REPLACE`), οπότε το παλιό γράφημα έδειχνε model Z σε ανοιχτές θέσεις. Π.χ. MAGIC/CTSI στις 02/10 14:00: 3,57 αντί για 4,19, σε 20 ώρες. Η σελίδα πλέον το ξαναχτίζει από τα trades, και ο κώδικας του bot δεν άλλαξε. (2) στα intraday το «Z · strategy» και το «Z · Binance live» είχαν το ίδιο χρώμα.
> - **Γραμμές entry/exit** (`tvcharts.z_levels`, `desk.rule_text`): όταν δεν έχεις θέση, «long entry» (πράσινη, −z_entry), «short entry» (κόκκινη, +z_entry) και οι έξοδοι (cyan). Σε θέση μόνο οι δικές της: το LONG βγαίνει όταν το Z ανέβει στο −z_exit και το SHORT όταν πέσει στο +z_exit, πάντα από τη μεριά που μπήκε. Η έξοδος στο 0 έχει πλέον ετικέτα (πριν δεν σχεδιαζόταν· και οι 4 ανοιχτές θέσεις έχουν z_exit = 0). Τα «±» στα κείμενα έγιναν «exit when Z falls to 0» κ.λπ.
> - **Διόρθωση 20:47 (ο χρήστης: «στο long spread γράφεις ό,τι να 'ναι»):** σε θέση, η γραμμή «entry» είναι πλέον το Z με το οποίο άνοιξε η θέση (`trades.z_entry`, π.χ. T/GRT −1,70) και όχι το όριο (−0,8). Ο άξονας κρατά πάντα ορατές τις γραμμές entry/exit (`series(include=)` → `autoscaleInfoProvider` στο `chart.js`· πριν, το exit 0 του T/GRT ήταν εκτός οθόνης). Σε θέση, το spread pane δείχνει entry/exit × το παγωμένο σ. Το «Z live» του ticker δεν βάφεται «hot» όταν υπάρχει θέση. ΣΕ ΛΕΙΤΟΥΡΓΙΑ (μόνο το dashboard). Προσοχή: αυτό το rsync ανέβασε και τοπικές αλλαγές 20:39–20:41 που δεν έγιναν σε αυτό το session (`config.py`, `core/signals.py`, `core/strategy.py`, `research/*`: `entry_rule: rolling_quantile`). Μπήκαν μόνο στο image του dashboard (129 tests περνούν, οι σελίδες δουλεύουν)· τα bots δεν έκαναν restart.
> - **Διόρθωση 21:06 (ο χρήστης για το bot B: «κάνω short, πέφτει το spread και έχω χασούρα»):** τα markers του Z (BUY/SELL/CLOSE) μπαίνουν πλέον στο Z των fills (`tvcharts.fill_z`, price markers `atPriceTop/Bottom/Middle`), όχι πάνω στη γραμμή στο κλείσιμο της ώρας. Το «Z at entry» (`data.pair_rows`: blotter, κάρτα θέσης, γραμμή entry) = Z των fills με το παγωμένο fit, όχι το Z του σήματος. Αιτία: το bot B ξεκίνησε 19:03 και μπήκε στις τιμές των 19:03 (OGN/ANKR fills Z 1,25), ενώ το σημείο των 19:00 είχε Z 1,56 και εκεί κρεμόταν το SELL· το Z έπεσε από 1,56 αλλά έμεινε πάνω από το 1,25. Επαληθεύτηκε: PnL ≈ −notional·σ·ΔZ (από τα fills) σε όλες τις θέσεις A και B. ΣΕ ΛΕΙΤΟΥΡΓΙΑ (μόνο το dashboard).
> - Έλεγχος σε πραγματικά δεδομένα: 1m Z έναντι των 5-second marks του bot, διάμεσος |ΔZ| 0,01, p95 0,02–0,04· 1h = 1m ακριβώς τις ίδιες στιγμές. 130 tests περνούν. Ελέγχθηκε σε headless Firefox (Spread Z 1h/1m, θέση SHORT, Model, Relative, χωρίς σφάλματα).

> **2026-10-05 (~20:30): προσαρμοστικά όρια εισόδου, ΓΡΑΦΤΗΚΑΝ ΓΙΑ BACKTEST (όχι στο bot ακόμα).** Ο χρήστης: «φτιάξε τα προσαρμοστικά όρια να τα δοκιμάσουμε».
> - **Απορρίφθηκε πριν γραφτεί:** «αναμενόμενο κέρδος 72 h ≥ 1,5 × κόστος». Στα 100 ζεύγη του test έβγαζε z* 0,08–0,94 (διάμεσος 0,26), γιατί το κόστος (30–117 bps μαζί με το funding) είναι μικρό μπροστά στο σ (2–26%). Θα έπεφτε πάντα στο κατώτατο όριο, άρα δεν θα ήταν προσαρμοστικό. Το ρίσκο είναι τα «σπασίματα», όχι τα κόστη.
> - **W1 (η ιδέα του χρήστη, «παράθυρο ημερών»):** `strategy.entry_rule: rolling_quantile`. Είσοδος όταν |Z| > 95ο εκατοστημόριο του |Z| των προηγούμενων 720 h, με ελάχιστο 1,5 (`core/signals.adaptive_entry_threshold`, χωρίς lookahead: shift 1). Έξοδος στο 0. `PairStrategy.check_entry(..., z_entry=)`· το backtest γράφει `z_threshold` σε κάθε trade. Στην περίοδο εκπαίδευσης, MAGIC/CTSI: p10 1,5 / διάμεσος 2,36 / p90 5,54.
> - **C1 (σύγκριση):** ίδια όρια για όλα τα ζεύγη, 2,0 / 0 (`research.fixed_z_entry/fixed_z_exit`).
> - Και τα δύο μοιράζονται τα ζεύγη και το δ της βαθμονόμησης του K1 (νέο `"calibrate_with"` στα VARIANTS), άρα αλλάζουν μόνο τα όρια. Νέα διαγνωστικά: `Stops%`, `MedianAbsZEntry`.
> - **Εντολή (ο χρήστης, ~30–40'), μαζί με το A/B του Kalman:** `python -m cryptopairs --profile kalman experiments --only K1 C1 W1 --out runs/experiments_ab`. Τρέχει K1, K1-1y, C1, W1.
> - **Κανόνας (γράφτηκε πριν):** αποφασίζει το Sharpe του validation· το test μόνο επιβεβαιώνει. Αν κερδίσει το W1 ή το C1, περνά πρώτα σε paper bot (π.χ. το B), μετά από υλοποίηση στο `engine` (το Z ιστορικό υπάρχει στο `_kalman` cache).
> - 129 tests περνούν (`tests/test_adaptive_thresholds.py`).
> - **ΑΠΟΤΕΛΕΣΜΑ (`runs/experiments_ab/summary.txt`, ο χρήστης το έτρεξε 2026-10-05):**
>
>   | Εκδοχή | Validation Sharpe / απόδοση | Test Sharpe / απόδοση | Stops % (val) | Διάμεσο \|Z\| εισόδου |
>   |---|---|---|---|---|
>   | **K1** (όλο το ιστορικό = B, ακριβές profile `kalman`) | **−0,30 / −6,5%** | **3,56 / +36,6%** | 27% | 1,71 |
>   | K1-1y (Kalman 1 έτους = A) | −0,52 / −9,0% | 2,78 / +28,5% | 27% | 1,66 |
>   | C1 (κοινά 2,0 / 0) | −0,42 / −7,7% | 3,09 / +30,7% | 37% | 2,22 |
>   | W1 (προσαρμοστικό 95% / 30 μέρες) | −0,68 / −10,9% | 2,66 / +25,0% | 32% | 1,85 |
>
>   - **Απόφαση με τον κανόνα: μένει το K1.** Τα W1 και C1 **απορρίπτονται**: χειρότερα και στο validation και στο test.
>   - **Το μοντέλο του B (όλο το ιστορικό) νικά το μοντέλο του A (1 έτος) και στις δύο περιόδους.** Ο κανόνας (1) της δοκιμής B ικανοποιείται· μένει το (2), δηλαδή 3 εβδομάδες paper.
>   - **Οι πραγματικοί αριθμοί της ρύθμισης του bot** (διόρθωση του λάθους #2 του audit) είναι πλέον −6,5% / +36,6%, όχι −9,7% / +27,5%. Χωρίς τις 10–11/10/2025, το validation θα ήταν **+14%**.
>   - **Διόρθωση της εκτίμησής μου:** τα όρια ανά ζεύγος δεν είναι απλώς θόρυβος. Σε επίπεδο χαρτοφυλακίου νικούν τα κοινά και τα προσαρμοστικά. Τα υψηλότερα όρια (C1 2,2, W1 1,85, έναντι 1,71 του K1) πιάνουν περισσότερα «σπασίματα» (stops 37% / 32% έναντι 27%) και κρατούν περισσότερο (141 / 128 h έναντι 77 h). Η συσχέτιση 0,05 αφορά την **κατάταξη** των ζευγών, όχι το επίπεδο του ορίου.

> **2026-10-05 (~21:30): το όριο ανά νόμισμα του bot B (`max_symbol_gross_leverage: 0.15`) δεν βοηθά στα δεδομένα εκτός δείγματος.** Αφορμή: χειροκίνητο short OGN/HOT στο B, που απορρίφθηκε (OGN gross 327,79 > 284,48). Έλεγχος: τα 100 ζεύγη του `runs/kalman/test_pairs.csv` (βαθμονόμηση ως 28/2), test 1/3–30/9/2026, profile kalman, 957 trades (script: scratchpad `third_leg.py`). Trades που άνοιξαν με ≥2 ανοιχτά σκέλη στο ίδιο νόμισμα: μέσος +2,7% έναντι +2,0% των υπολοίπων (διαφορά +0,9 pp, 90% −2,0..+3,8, όχι σημαντική). Χαρτοφυλάκιο (PnL στο κλείσιμο, 100 ανά ζεύγος): χωρίς όριο PnL 3.102, max DD −947, Sharpe 3,88· με όριο 2 σκελών 1.503 / −492 / 3,08. Μόνο τα 20 live ζεύγη: 961 / −302 / 2,48 έναντι 760 / −254 / 2,10. Δηλαδή το όριο απλώς μικραίνει το μέγεθος, όπως μικρότερο leverage, και ο λόγος κέρδους προς ρίσκο πέφτει λίγο. Όρια του ελέγχου: μόνο realized PnL· ένα παράθυρο 7 μηνών χωρίς crash όλης της αγοράς. Πρόταση προς τον χρήστη: αφαίρεση του ορίου από το B (ίδιο με το A, 2,0) με restart μόνο του `bot_b`· περιμένει έγκριση. **~21:40:** ο χρήστης ζήτησε 100 ζεύγη. `research --final --tag kalman_b100 --top 100` → `runs/kalman_b100/pairs_all100.yaml`. Έλεγχος lots (scratchpad `lots_check.py`, 20 USDT/ζεύγος, 2x, κανόνες Binance + `round_to_lots`): 90 περνούν· 6 εκτός Binance (LRC, DENT, HFT), 4 με πολύ χοντρό lot (YFI ×3, AVAX) → `runs/kalman_b100/pairs.yaml` = 90 ζεύγη, 46 νομίσματα (OGN σε 17, BAND σε 13). Ταυτόχρονες θέσεις στο test 100 ζευγών: διάμεσος 39, max 54. Το άλλο session (test-bot-algorithm-fixes): το B έχει `realtime_exits` live από 21:18 και 5 θέσεις· η αξιολόγησή του ~26/10. Πρόταση: ξεχωριστό bot C με τα 90, A και B ανέγγιχτα· περιμένει απόφαση. Server: ~2,2 GB ελεύθερη μνήμη, ~340 MB ανά bot.

> **2026-10-05 ~22:00: το bot B τρέχει πλέον 93 ζεύγη (ΣΕ ΛΕΙΤΟΥΡΓΙΑ).** Ο χρήστης: «να μπουν στο B χωρίς να το πειράξεις».
> - `runs/kalman_b/pairs.yaml` = τα 20 ζεύγη του B αυτούσια (αντίγραφο στο `pairs_20.yaml`) + τα επόμενα 74 του `research --final --tag kalman_b100 --top 100` (ίδιο funnel και βαθμονόμηση, ίδιες παράμετροι στα κοινά 20). Εκτός: LRC/DENT/HFT (δεν διαπραγματεύονται πια) και CTSI/AVAX (lot). Το OGN/LRC μένει στη λίστα και το bot το παραλείπει.
> - Restart του `bot_b` χωρίς `--fresh` στις 21:58:51: 93 ζεύγη, 47 νομίσματα, 6 θέσεις διατηρήθηκαν, equity 9.298,75. Το config **δεν άλλαξε**: €100 ανά ζεύγος, όπως τα υπάρχοντα. Το όριο 0,15 ανά νόμισμα είναι πλέον ~€1.395 (~14 σκέλη), επειδή είναι ποσοστό του equity. `max_open_pairs` 50· στο test με 100 ζεύγη ήταν >50 στο 4% των ωρών. Οι άμεσες έξοδοι συνεχίζουν. Το bot A δεν άλλαξε.
> - Πρώτη ώρα (22:00): 11 νέες θέσεις, κυρίως short σε BAND ×3, ARPA ×3, OGN ×3. Χωρίς λάθη.
> - Dashboard: `data.capital_added` (από τα events `session_start`) + `data.with_capital`. Κεφάλαιο που μπαίνει αργότερα μετρά ως κατάθεση, όχι ως κέρδος, στα equity, equity_live, day_start και `load_nav`. Επαληθεύτηκε: η καμπύλη του B μένει ~9.299 στο restart (raw 1.900 → 9.299). Ανέβηκε μόνο το `data.py` (rsync ενός αρχείου).
> - **Διόρθωση 22:12** (ο χρήστης: «το equity chart είναι παράλογο»). Το chart του B έδειχνε high-water mark 16.697 και drawdown −44%. Αιτία: στην εκκίνηση το bot γράφει το equity της τελευταίας κλεισμένης ώρας με τη χρονοσφραγίδα εκείνου του bar, αλλά με το νέο equity, και η προσαρμογή πρόσθετε ξανά τις 7.400. Πλέον κάθε γραμμή κρίνεται με βάση τη συνεδρία που τη γράφει (`session_id`: max(ώρα, έναρξη συνεδρίας)), και η κατάθεση μετρά από την έναρξη της συνεδρίας που πρόσθεσε τα ζεύγη. B μετά τη διόρθωση: HWM 9.302, max DD −0,04%. Ανέβηκε μόνο το `data.py`· 137 tests περνούν.
> - Η πρώτη αξιολόγηση μετά το restart άνοιξε 20 θέσεις με maker-first (market μετά από 300 s) και τελείωσε 19:04 UTC, οπότε η απόφαση της ώρας 22:00 Αθήνας καθυστέρησε ~5'. Συμβαίνει μόνο μία φορά.
> - Δοκιμή B: οι ίδιοι κανόνες, με 93 ζεύγη από τις 5/10 22:00· αξιολόγηση ~26/10 έναντι του A, σε %.
> - Παράλληλα, το session `test-bot-algorithm-fixes` έβαλε στο Pair desk ιστορικό Z/spread από το archive (`ui/history.py`, deploy του dashboard 21:59).

> **2026-10-05 ~23:20: funding με ένα πρόσημο παντού στο dashboard. ΣΕ ΛΕΙΤΟΥΡΓΙΑ** (μόνο το dashboard· τα bots δεν άλλαξαν). Ο χρήστης μπερδευόταν: το «+» σήμαινε σε άλλα σημεία ποσοστό («πληρώνουν τα long»), σε άλλα «πήρες» και σε άλλα «πλήρωσες».
> - Ποσά = κέρδος/ζημιά: **+ = πήρες, − = πλήρωσες**, οι προμήθειες πάντα −. Ισχύει στα `data.trade_view`, `lab.trades_frame`, στο ιστορικό του ζεύγους, στη στήλη «fees + funding» του Overview και στο Backtest lab.
> - Τα ποσοστά γράφουν δίπλα «you pay / you receive» (`data.funding_word`), και χωρίς θέση «longs pay / shorts pay». Το βιβλίο εντολών γράφει «you pay» αντί για «we pay».
> - Κάρτα «Funding» στο Pair desk: σε θέση, USDT/ημέρα συνολικά και ανά νόμισμα (`fund.funding_day`)· π.χ. B OGN/ANKR −1,59/day (OGN −1,56, ANKR −0,03).
> - Επαληθεύτηκε ότι τα κόστη είναι σωστά: προμήθειες 0,05% / 0,02% (OGN/ANKR, άνοιγμα 0,0690 = 0,0206 + 0,0483) και funding από τις πραγματικές εκκαθαρίσεις της Binance. Μικρή προσέγγιση: εκκαθάριση στην επεξεργασία του επόμενου bar, με την τιμή εκείνης της στιγμής.
> - **Σημαντικό:** το OGN εκκαθαρίζει κάθε **4 h** και από τις 05/10 έχει rate ~−0,26…−0,29%. Το B είναι short OGN σε 5 ζεύγη και long σε 1, οπότε πληρώνει ~7,2 USDT την ημέρα σε funding. Το bot δεν λαμβάνει υπόψη το funding στις εισόδους (`funding_filter` off). Προς το παρόν παρακολούθηση.
> - Δεν υπάρχει spot: και τα δύο σκέλη είναι USDⓈ-M perpetuals με leverage 2. Το `costs.long_leg: spot` υπήρξε μόνο ως εκδοχή backtest (Sharpe 0,69) και δεν υιοθετήθηκε.

> **2026-10-05 (~23:45): το κλικ σε ζεύγος στο sidebar ανοίγει το Pair desk του. ΣΕ ΛΕΙΤΟΥΡΓΙΑ** (μόνο το dashboard· τα bots δεν άλλαξαν). Ο χρήστης: «όταν πατάω το pair στο sidebar να με πετάει στο pair desk του επιτόπου».
> - `widgets._watchlist`: κάθε κλικ, και στο ζεύγος που είναι ήδη focus, ορίζει `_focus_pick` + `_open_desk`. Το `dashboard.main()` κάνει `st.switch_page` στη σελίδα «pair» όταν δεν είμαστε ήδη εκεί.
> - `js/watchlist.js`: όσο τρέχει το app (`stStatusWidget` στο DOM), το Streamlit πετά το κλικ, και η επανάληψη το ξαναξεκινά. Μετρήθηκε: με επαναλήψεις κάθε 3 s, ένα κρύο Overview κόλλησε 45 s. Γι' αυτό το κλικ **περιμένει να γίνει idle το app** και στέλνεται μία φορά. Ξαναστέλνεται μόνο όταν το app είναι idle, ανά 4 s, έως 5 φορές μέσα σε 60 s. Το ζεύγος που περιμένει μένει επισημασμένο.
> - Επαληθεύτηκε σε headless Firefox: τοπικά (ζεστό ~3 s, κρύο με κλικ στην αρχή → Pair desk μετά τη φόρτωση) και στον server μέσω Tailscale (~6 s). Test: `test_a_click_in_the_watchlist_opens_that_pairs_desk`. 140 tests περνούν.
> - **Απόδοση:** κρύο Overview με το kalman_b (93 ζεύγη) ~42 s στο PC και ~20 s στον server· ζεστό ~9 s. Το πρώτο Spread Z ζεύγους του A δείχνει «Loading the spread's history…» για λίγα δευτερόλεπτα (αναπαραγωγή παραθύρου 1 έτους, cache 6 h).
> - **Ανοιχτά από το audit του dashboard του B (2026-10-05, δεν έχουν διορθωθεί):**
>   - Max drawdown «0,00% none yet», ενώ το πραγματικό είναι −0,04%: το `drawdown_episodes` κρατά μόνο ≥ 0,1%·
>   - Kill switch «33,7% away», σωστό 25,2%: `head = nav/kill − 1` αντί για `1 − kill/nav`·
>   - «Cost saved vs taker» με σταθερά 12,5 bps, ενώ η πραγματική άμεση market είναι ~7 bps: το maker-first του B κόστισε 11,3 bps·
>   - Volatility από 2 μόλις ώρες δεδομένων.

> **2026-10-06 (~13:00): κλικ στη watchlist άμεσα + το timeframe δεν αλλάζει πια ζεύγος + κινητό. ΣΕ ΛΕΙΤΟΥΡΓΙΑ** (μόνο το dashboard, `docker compose up -d --build --no-deps dashboard`· τα bots δεν έκαναν restart).
> - Αιτία: η watchlist είναι μέσα σε fragment με `run_every` 1 s. Το Streamlit πετούσε το trigger του fragment όσο έτρεχε η σελίδα, οπότε το κλικ αργούσε 20–42 s ή χανόταν. Ένα παλιό κλικ έφτανε αργότερα, π.χ. μαζί με ένα κλικ σε timeframe, και άλλαζε το ζεύγος (π.χ. GTC/BAND → KNC/ARB). Μετρήθηκε στον παλιό κώδικα: 13 αλλαγές ζεύγους σε 36 αλλαγές timeframe.
> - Διόρθωση: `ui/js/relay.js` + `tv.pick_relay()`, ένα αόρατο component έξω από κάθε fragment, που καλείται στο `widgets.sidebar`. Η watchlist στέλνει μέσω `window.__cpPick`, και το trigger του relay είναι κανονικό rerun της εφαρμογής. Ένα κλικ οπουδήποτε αλλού ακυρώνει ένα κλικ ζεύγους που περιμένει. Στον server: άνοιγμα σε ~3 s, 0 αλλαγές ζεύγους σε 20 αλλαγές 1h ↔ 15m.
> - Κινητό: `initial_sidebar_state="auto"` (κλειστό σε κινητό), κάρτες KPI 2 ανά γραμμή κάτω από 700 px (`theme.py`), μεγαλύτερες γραμμές watchlist σε οθόνη αφής (`tv.py`).
> - Έλεγχος: Marionette client στο scratchpad (`mn.py`, `t5.py`, `t6.py`). 140 tests περνούν. Χωρίς commit.

> **2026-10-06 (~14:00): γρήγορη απόκριση στα κλικ. ΣΕ ΛΕΙΤΟΥΡΓΙΑ** (μόνο το dashboard· τα bots δεν έκαναν restart). Σχέδιο: `~/.claude/plans/lazy-gliding-wozniak.md`.
> - **Μετρήσεις στον server, μέσω Tailscale:**
>
>   | Ενέργεια | Πριν | Μετά |
>   |---|---|---|
>   | Αλλαγή ζεύγους → τίτλος + ticker | 1,3 s | **0,02–0,09 s** (στον browser) |
>   | Αλλαγή ζεύγους → βιβλία | 5,7–8,6 s | **0,3–0,55 s** |
>   | Αλλαγή ζεύγους → κάρτες + γράφημα | 5,7–8,6 s | 0,7–3,3 s |
>   | Αλλαγή timeframe | ~1–2 s | 0,65–2,3 s (τοπικά 0,22 s) |
>
>   Τοπικά, στο ίδιο αντίγραφο της βάσης: όλο το desk σε 0,3–0,6 s.
> - **Καμία κλήση Binance πάνω στο κλικ:**
>   - `ui/swr.py`: «δώσε την τελευταία τιμή, ανανέωσε στο παρασκήνιο», με pool 4 threads. Τα failures δεν αποθηκεύονται και δεν ξαναδοκιμάζονται για 10 s.
>   - `market.instruments`: ένα snapshot όλης της αγοράς (`fetch_tickers()` + `fetch_funding_rates()`, 15 s), OI στο παρασκήνιο.
>   - `candles` / `order_books` / `lab.recent_bars` μέσω swr. Το `recent_bars` χρησιμοποιεί πλέον το κοινό `M.exchange()`· πριν έφτιαχνε νέο ccxt με `load_markets` σε κάθε κλήση.
> - **Προθέρμανση** (`history.warm`, καλείται από `desk.warm(ctx)` στο `dashboard.main`): για όλα τα ζεύγη, 4 παράλληλα, κάθε 1 h, φέρνει κεριά 1h και τα bars μετά το archive. Για τις ανοιχτές θέσεις υπολογίζει και το `model_history` (max_entries 64, ~3 MB το καθένα). `pair_history` max_entries 32.
> - **`pair_rows` μία φορά ανά έκδοση της βάσης:** το `load_core` γράφει `_key`, και το `pair_rows` κάνει cache το αποτέλεσμα. Πριν τρεχόταν 5 φορές ανά rerun.
> - **Pair desk:**
>   - τα κουμπιά Chart / Timeframe, το γράφημα, το βιβλίο και το tape είναι σε δικό τους fragment (`chart_panel`, `run_every` max(refresh, 5)), άρα ένα timeframe ξανατρέχει μόνο αυτό. Τα κουμπιά βρίσκονται πλέον ακριβώς πάνω από το γράφημα, κάτω από τις κάρτες;
>   - όλα τα timeframes του ζεύγους φορτώνουν στο παρασκήνιο;
>   - το βιβλίο του ticket φορτώνει παράλληλα (`prefetch_books`);
>   - `CANDLES` → `market.CANDLES`.
> - **Αλλαγή ζεύγους στον browser:**
>   - το relay κουβαλά `market_data` για όλα τα ζεύγη (`widgets.market_data` από `D.load_pair_states`· ξαναστέλνεται μόνο σε πλήρες rerun, όχι κάθε 1 s, για να μην τρώει δεδομένα στο κινητό);
>   - το κλικ στέλνει event `cp-pair`, και ticker / βιβλία / tape (`market.js`) και τίτλος αλλάζουν αμέσως. Το `cp-pair-cancel` γυρίζει πίσω αν σε 20 s δεν έρθει το νέο desk;
>   - test: τα `market_data` της watchlist = `PairState.market_data()`.
> - **Hub της Binance (`common.js`):** SUBSCRIBE/UNSUBSCRIBE στην ίδια σύνδεση αντί για νέα σύνδεση σε κάθε αλλαγή. Ένα REST snapshot ανά νέο σύμβολο (depth, bookTicker, premiumIndex, aggTrades), με CORS που η Binance επιτρέπει. Τα δεδομένα ενός συμβόλου που φεύγει από τη σελίδα σβήνονται.
> - **Ένδειξη στο κλικ:** μπάρα + «Loading X / Y…»· σβήνουν οι κάρτες και το γράφημα (όχι τα live panels) μέχρι να εμφανιστεί το `pd_book_<pid>`.
> - **CPU:** η ανανέωση 1 s κοστίζει ~48% ενός πυρήνα ανά ανοιχτό tab (τοπικά). Με 5 s κοστίζει 13%, και με off 0%. Ο server είναι στο όριο: load 2,4–3,0 σε 2 πυρήνες, bot B 68–77%, dashboard 45–67%. Γι' αυτό ο server είναι πιο αργός από το PC στις κάρτες και στο γράφημα. Η απόφαση για την ανανέωση ανήκει στον χρήστη.
> - 145 tests (`tests/test_swr.py`). Έλεγχοι σε headless Firefox, με scripts στο scratchpad (`mn.py` Marionette, `t4`/`t6`/`t7`/`t8`/`t9`, `restart.sh`): 0 αλλαγές ζεύγους σε 20 αλλαγές timeframe, κινητό ΟΚ. Χωρίς commit.
> - **~15:15: έξυπνη ανανέωση (ο χρήστης τη διάλεξε). ΣΕ ΛΕΙΤΟΥΡΓΙΑ** (μόνο το dashboard).
>   - Πώς δουλεύει: `ui/pulse.py` είναι ένα thread που γράφει το `data.version` κάθε βάσης στο `static/ver/<db>.json` όταν αλλάζει (ελέγχει 4 φορές το δευτερόλεπτο). Το `js/pulse.js` το ελέγχει κάθε «Live update» s (στατικό αρχείο, χωρίς Python) και ζητά rerun μόνο από τα fragments που ζωγραφίστηκαν με παλιότερη έκδοση.
>   - Τα fragments ρωτιούνται **ένα τη φορά**, γιατί το Streamlit πετά ένα rerun fragment όσο τρέχει άλλο. Με όλα μαζί η καθυστέρηση ήταν 0,5–4,9 s.
>   - Στον κώδικα: `W.every(refresh)` = max(refresh, 15 s) ως fallback για ρολόγια και ηλικίες, και `W.pulse(...)` στην αρχή κάθε live fragment. Το `.st-key-pulse_*` / `.st-key-pick_relay` είναι κρυμμένο. Το `static/ver/` μπήκε σε `.gitignore` / `.dockerignore`.
>   - **Μετρήσεις στον server:** εγγραφή του bot → σελίδα ενημερωμένη σε 0,9–1,6 s. CPU του dashboard ~2% σε ηρεμία, με κορυφές στις εγγραφές (πριν 45–67% συνεχώς). Τοπικά: 48% → 14–22% ανά tab.
>   - **Κλικ σε ζεύγος στον server:** τίτλος + ticker 0,01–0,09 s, βιβλία 0,3–0,4 s, όλο το desk 0,6–1,7 s (μία φορά 12,9 s, αμέσως μετά το restart, όσο έτρεχε η προθέρμανση). **Timeframe:** διάμεσος 0,67 s, max 1,32 s.
>   - Ο server μένει φορτωμένος από το **bot B (66–98% CPU)**: load ~2,5–3 σε 2 πυρήνες.

> **2026-10-06 (~16:00): νέα σελίδα «Journal» (λίστα trades + φίλτρα). ΣΕ ΛΕΙΤΟΥΡΓΙΑ** (μόνο το dashboard). Ο χρήστης διάλεξε μόνο τη λίστα με φίλτρα, χωρίς κάρτα ανά trade, σημειώσεις ή στατιστικά.
> - `ui/views/journal.py` + `data.journal(d, rows, realtime)`: όλα τα trades του bot, τα νεότερα πρώτα.
>   - Στήλες: #, ζεύγος, πλευρά, status, origin (signal/manual), είσοδος, έξοδος, ώρες, Z εισόδου, «Z exit / now», λόγος εξόδου, PnL, PnL %, fees, funding (+ πήρες / − πλήρωσες).
>   - Z εξόδου = (ln exit_a − α − β ln exit_b) / σ με το fit του trade.
>   - Λόγος εξόδου: «exit signal · hourly» ή «· instant» (από τα events `realtime_exit`, που φορτώνονται όλα, ±10 λεπτά), stop-loss, manual.
>   - Ανοιχτά trades: uPnL και Z τώρα από το `pair_rows`.
> - Φίλτρα: ζεύγος, πλευρά, status, κέρδος/ζημιά, λόγος εξόδου, origin, περίοδος (All / Today / 7 / 30 μέρες), και μία γραμμή με σύνολα. Ακολουθεί το bot του sidebar και το «Pairs in view». Έξυπνη ανανέωση (pulse).
> - Στον server: 45 trades του B, 8 κλεισμένα (8 κέρδη, +44,48 USDT, fees −1,11, funding +1,32), 37 ανοιχτά. 158 tests (`tests/test_journal.py`, και η σελίδα στο `test_every_page_renders`). Έγινε επίσης `docker builder prune` στον server (−4,8 GB· δίσκος 32 GB ελεύθερα).

> **2026-10-06 (~17:00): άμεσες είσοδοι με φίλτρα καρφιάς/momentum. ΔΟΚΙΜΗ ΜΟΝΟ (κανένα bot δεν άλλαξε). Αποτέλεσμα: ΔΕΝ υιοθετείται, με τον κανόνα που γράφτηκε από πριν.** Σχέδιο: `~/.claude/plans/lazy-gliding-wozniak.md`.
> - **Αφορμή:** ο χρήστης είδε το ARPA/HOT να περνά το όριο χωρίς είσοδο. Αιτίες:
>   - το bot ελέγχει εισόδους μόνο στο κλείσιμο της ώρας;
>   - στις 15:00 το Z στο κλείσιμο ήταν 2,06 (όριο 2,0), αλλά το B αποφάσισε ~20–40 s αργότερα με live τιμές, όταν το Z ήταν 1,88–1,99. Από 322 κλεισίματα πέρα από το όριο, είναι η μόνη τέτοια περίπτωση, και τα άλλα 14 είναι το RLC/OGN πάνω από `max_entry_z` 10;
>   - τις τελευταίες 24 h, σε 16 περιπτώσεις το Z πέρασε το όριο μέσα στην ώρα, και στις 14 είχε γυρίσει μέχρι το κλείσιμο.
> - **Δεδομένα:** κεριά 1 λεπτού στο `data/klines/1m/` (66 νομίσματα, 08/2025–09/2026, ~2,3 GB, λήψη ~6 λεπτά με τον υπάρχοντα `DataStore(interval="1m")`). Μόνο στο PC.
> - **`research/intrabar.py`:** επανάληψη ανά λεπτό. Ωριαίο μοντέλο και κόστη όπως το backtest, fills στο άνοιγμα του επόμενου λεπτού, έξοδοι ανά λεπτό (= B), μία ενέργεια ανά bar, ζεύγη και παράμετροι του K1 (`runs/experiments_ab`).
>   - Η εκδοχή «replica» (ωριαίες είσοδοι και έξοδοι) = K1 **ακριβώς**: −0,30 / −6,5%, 998 trades και 3,56 / +36,6%, 1.093 trades.
>   - Προσοχή: το `data_start` εξαρτάται από το παράθυρο (`min_history_days` 790 στο validation), γι' αυτό παίρνεται από το `experiments.STAGES`.
>   - Tests: `tests/test_intrabar.py`, όπου η replica = `run_backtest` σε συνθετικά δεδομένα. Αποτελέσματα στο `runs/intrabar/`.
>
>   | Εκδοχή | Val Sharpe / απόδοση / stops | Test Sharpe / απόδοση / stops |
>   |---|---|---|
>   | hourly (= B σήμερα: ωριαίες είσοδοι, έξοδοι ανά λεπτό) | 0,70 / +5,6% / 21,6% | **3,33 / +32,9%** / 19,7% |
>   | hourly_cap (+ καμία είσοδος αν \|Z\| > όριο+1,5) | 1,03 / +7,1% / 19,3% | 2,69 / +21,5% / 18,1% |
>   | i0 (άμεση, χωρίς φίλτρο) | **1,55 / +12,8%** / 18,8% | 2,94 / +29,6% / 17,5% |
>   | i5 (5 λεπτά πέρα) | 1,25 / +9,6% / 20,5% | 2,58 / +25,3% / 19,3% |
>   | turn (γύρισμα 0,3) | 1,14 / +8,0% / **24,0%** | 2,56 / +23,6% / 21,1% |
>   | turn_cap | 0,56 / +3,7% / 20,8% | 2,42 / +18,6% / 18,6% |
>
> - **Κανόνας:**
>   - νικητής στο validation είναι το i0 (το turn αποκλείεται: περισσότερα stops από το hourly);
>   - στο test όμως το i0 είναι χειρότερο από το hourly (2,94 έναντι 3,33), άρα **δεν υιοθετείται**, και οι είσοδοι μένουν ωριαίες;
>   - καμία εκδοχή λεπτού δεν νικά το hourly στο test.
> - **Παράπλευρα ευρήματα:**
>   1. Οι **άμεσες έξοδοι** του B (hourly έναντι replica) βελτιώνουν πολύ το validation (0,70 / +5,6% έναντι −0,30 / −6,5%) και ελάχιστα χειροτερεύουν το test (3,33 έναντι 3,56). Στηρίζει το B.
>   2. **Ο φόβος του χρήστη επιβεβαιώνεται σε επίπεδο trade:** στο i0, όταν το Z είχε φύγει > όριο+1,5, τα stops ήταν 48% / 44% (val/test) έναντι 16% / 16%, και ο μέσος όρος −1,3% / +0,7% έναντι +0,7% / +1,5%. Όμως η εκδοχή με ανώτατη απόσταση (cap) χάνει στο test σε επίπεδο χαρτοφυλακίου, άρα δεν υιοθετείται.
>   3. Το «γύρισμα» (turn) **αύξησε** τα stops (24%): η αναμονή να γυρίσει το Z δεν προστατεύει.

## 1. Εκκρεμότητες (με σειρά προτεραιότητας)

**Για το επόμενο session** (ο χρήστης, 2026-10-05: «τα κρατάμε για επόμενο session»):
1. **Φρουρός funding** (το πιο επείγον: χάνονται λεφτά από τώρα). Το OGN εκκαθαρίζει ανά 4 h με ~−0,3%, και το B πληρώνει ~€7–12 την ημέρα στα short OGN. Πρόταση: καμία είσοδος, ή έξοδος, όταν το αναμενόμενο funding μέχρι την έξοδο ξεπερνά το αναμενόμενο κέρδος. Πρώτα δοκιμή στο backtest (το ιστορικό funding υπάρχει), με τον κανόνα validation/test.
2. **Οι 4 διορθώσεις του dashboard** από το audit (βλ. το entry 23:45 παραπάνω): Max drawdown, Kill switch away, Cost saved vs taker, Volatility.
3. **Όριο ανά νόμισμα στο B:** το 0,15 × €9.300 (~€1.395) δεν ενεργοποιείται πια. Καθαρό short ARPA ~€600, OGN ~€430, COMP ~€410. Απόφαση του χρήστη (π.χ. ~0,03 για 2–3 θέσεις ανά νόμισμα).
4. **Εκτέλεση:** το maker-first του B κόστισε 11,3 bps, ενώ μια άμεση market ~7 bps (26 εκτελέσεις). Να παρακολουθηθεί και να συγκριθεί ανά εντολή.
5. **Αξιολόγηση A/B γύρω στις 26/10**, με τον κανόνα του entry της 2026-10-05.
6. **Χωρίς commit:** οι αλλαγές dashboard του session «funding-rate-sign-convention» (data, desk, fund, market.js, lab, backtest, overview, pair, test_fund) και οι δικές μου (watchlist → Pair desk: widgets, dashboard, watchlist.js, test_dashboard_controls). Είναι όλες deployed στον server. Commit + push όταν το ζητήσει ο χρήστης.


0. ~~Bot κάτω~~: **ξανά σε λειτουργία από τις 22:38** (PID 204313), με 4 ανοιχτές θέσεις (νέα: BAND/GTC LONG). Dashboard PID 204663 (22:39).
   Ιστορικό του προβλήματος: **το bot δεν έτρεχε από τις 22:03:38.** Η αιτία βρέθηκε στις 22:35: ο χρήστης έγραφε `--pairs runs/kalmann/pairs.yaml`, με δύο «n» (FileNotFoundError, και στις 22:31 και στις 22:33). Πλέον:
   - το `--pairs` είναι προαιρετικό: η προεπιλογή είναι `runs/<profile>/pairs.yaml`, οπότε αρκεί `python -m cryptopairs --profile kalman paper`;
   - ένα λάθος όνομα δίνει «did you mean» και σταματά πριν ανοίξει συνεδρία;
   - το `load_markets` ξαναδοκιμάζει 5 φορές;
   - κάθε σφάλμα εκκίνησης γράφεται στο `logs/paper.log`;
   - το dashboard δείχνει κόκκινο banner «The bot is not running».
   Το dashboard (PID 182974, 22:03) χρειάζεται επίσης restart: έχει φορτωμένα παλιά modules και κάποιες σελίδες βγάζουν σφάλμα «no attribute 'kpis'».
0b. **Επανεκκίνηση bot ΚΑΙ dashboard, χωρίς `--fresh`** (μετά το dashboard v3, 2026-10-01 ~21:45):
   - το bot χρειάζεται τις νέες εντολές `set_params` / `clear_params`· ένα παλιό bot τις απορρίπτει ως «unknown command»;
   - το dashboard διαβάζει το σκούρο θέμα (`.streamlit/config.toml`) μόνο στην εκκίνηση, και πρέπει να ξεκινά μέσω `python -m cryptopairs dashboard`.
   - Έλεγχος: μπάρα πλοήγησης πάνω (Overview, Pair monitor, Backtest lab, Executions, Risk & controls, System logs), σκούρο φόντο.
   - Στις 21:34 ο χρήστης νόμιζε ότι έκανε restart, αλλά οι διεργασίες ήταν οι ίδιες: bot PID 155770 (pts/7), dashboard PID 156136 (pts/6). Το `kill <pid>` κλείνει το bot ομαλά (SIGTERM), όπως το Ctrl+C.
   - Live χωρίς «refresh» (22:00): το bot γράφει live στιγμιότυπα κάθε **5 s** (διατήρηση 24 h, ήταν 15 s / 72 h) και το dashboard ανανεώνεται κάθε 2 s. Το static serving (`plotly.min.js`) διαβάζεται μόνο στην εκκίνηση· χωρίς restart τα γραφήματα φορτώνουν το plotly.js από CDN.
1. ~~Επανεκκίνηση bot και dashboard~~: **ΕΓΙΝΕ** 2026-10-01 20:51. Schema v3 ενεργό, `fit_history` = 12.240 γραμμές (30 μέρες × 24 h × 17 ζεύγη), `pair_live` γράφεται κάθε 15 s, 3 θέσεις αποκαταστάθηκαν. Νέα συνεδρία: `paper_20261001_175113_619416`.
   - **Μένει:** δοκιμή ενός κουμπιού (π.χ. «Pause entries for this pair»), που πρέπει να δώσει `done` σε ~2 s. Ο πίνακας `commands` είναι ακόμα άδειος.
2. **Paper trading για 2–4 εβδομάδες.** Συγκρίνουμε:
   - με το backtest;
   - ποσοστό maker και κόστος σε bps (καρτέλα Executions);
   - funding.
3. **Νέα δεδομένα και ξαναβαθμονόμηση πριν από οποιοδήποτε live.** Τα ζεύγη του `runs/kalman/pairs.yaml` βαθμονομήθηκαν με δεδομένα **μέχρι 2026-02-28**, και τα τοπικά δεδομένα φτάνουν ως **2026-09-30**.
4. **Φάση 3b: live εκτέλεση** (δες §9). Δεν έχει ξεκινήσει.
5. **Docker/VPS (έτοιμο, περιμένει server).** Ο χρήστης θα νοικιάσει VPS: προτάθηκε **OVHcloud VPS-1** (2 vCores, 4 GB, 40 GB, Γαλλία/Γερμανία, μηνιαία χωρίς δέσμευση), γιατί τα φθηνά πακέτα της Hetzner είναι «not available» από τον Ιούνιο 2026 και τα υπόλοιπα κοστίζουν πλέον ≥ €12/μήνα. Στο OVH ο χρήστης είναι `ubuntu`, και το `DEPLOY.md` βήμα 2 αντιγράφει το κλειδί στον `root`. Αποφασίστηκε 2026-10-01: bot **και** dashboard στον ίδιο server (το dashboard διαβάζει τη SQLite του bot και γράφει εκεί τις εντολές των κουμπιών, άρα όχι Streamlit Cloud). Το `docker-compose.yml` έχει πλέον δύο services (`bot`, `dashboard`), ίδιο image με τα `requirements-dashboard.txt`. Το dashboard ακούει μόνο στο `127.0.0.1:8501` του server, και ο χρήστης το βλέπει με `ssh -N -L 8502:127.0.0.1:8501 cryptobot` → http://localhost:8502. Το `./deploy.sh` κάνει rsync τον κώδικα και `docker compose up -d --build`. Οδηγός βήμα-βήμα: `DEPLOY.md` (περιλαμβάνει μεταφορά του `state/paper.db*` για να συνεχιστεί η συνεδρία, και προαιρετικό rsync του `data/` για το Backtest lab). **Το Docker build δεν έχει δοκιμαστεί ακόμα** (δεν υπάρχει Docker τοπικά). Θα δοκιμαστεί πάνω στον server. Τοπικά επαληθεύτηκε μόνο η εντολή του dashboard σε headless mode (`--no-browser`, `STREAMLIT_SERVER_FILE_WATCHER_TYPE=none`): health `ok`, σελίδα 200, plotly.js 200.
6. Προαιρετικό, συζητήθηκε: **PCA στο funnel** (συσχέτιση αφού αφαιρεθεί ο «παράγοντας αγορά»), ως **μία** προκαθορισμένη εκδοχή με το πρωτόκολλο validation/test. Ο χρήστης ενδιαφέρθηκε, αλλά συμφωνήσαμε να προηγηθεί το paper.

---

## 2. Περιβάλλον

- Φάκελος: `/home/rigojohn/Documents/CryptoPairsBot`. Venv: `.venv` (Python 3.12). Git repo (branch `main`), remote: private `https://github.com/Rigojohn/Pairs-Trading-Mean-Reversion` (το παλιό του περιεχόμενο αντικαταστάθηκε με force push στις 2026-10-01). Δεν ανεβαίνουν: `data/`, `state/`, `logs/`, το PDF, το zip, και από το `runs/` μόνο τα `pairs.yaml`.
- Υπολογιστής χρήστη: i5-4590 (4 πυρήνες), 7,7 GB RAM (~2,7 GB ελεύθερα), Linux.
  - Ένα backtest 2 ετών παίρνει **~50 ms**.
  - Βαθμονόμηση 100 ζευγών: **~2 λεπτά** (3 workers).
  - Πλήρες πείραμα validation+test: **~20 λεπτά**.
- Βασικές βιβλιοθήκες: numpy 2.5.3, pandas 3.0.6, ccxt 4.5.85 (με `ccxt.pro`), PyYAML, streamlit 1.64, plotly 7.1, pytest, statsmodels (μόνο για το τεστ σύγκρισης με τον αρχικό κώδικα).
- Δίκτυο: η Binance (fapi, data.binance.vision, WebSocket) είναι προσβάσιμη. CoinGecko χωρίς API key (λειτούργησε, 50 από 197 νομίσματα δεν αντιστοιχίστηκαν).
- Tests: `python -m pytest -q` (~22 δευτερόλεπτα, **111 passed**).
- Μνήμες του Claude για το project: `~/.claude/projects/-home-rigojohn-Documents-CryptoPairsBot/memory/` (φορτώνονται αυτόματα). Τελευταίο plan: `~/.claude/plans/kalman-fuzzy-ullman.md`.

---

## 3. Δομή κώδικα (σύνοψη)

```
cryptopairs/
  core/        ΚΟΙΝΟΣ πυρήνας για backtest, paper και live (χωρίς I/O)
    signals.py   rolling OLS, Kalman filter (α, β, σ), Z με bid/ask, half-life
    sizing.py    βάρη 1/(1+β) και β/(1+β), στρογγυλοποίηση σε lots, έλεγχος αντιστάθμισης
    book.py      λογιστική ζεύγους (cash, qty, fees, funding, equity)
    strategy.py  PairStrategy: όλοι οι κανόνες (είσοδος, έξοδος, stops, cooldown,
                 απενεργοποίηση, φίλτρα), snapshot/restore
  research/    offline
    data.py      λήψη klines/funding από data.binance.vision (και delisted), PairData
    metadata.py  CoinGecko: blockchain κάθε νομίσματος
    funnel.py    φίλτρα 1-4 του paper (+ select_pairs με όριο ανά νόμισμα)
    backtest.py  event-driven backtest με κόστη (taker/maker, spread, slippage, funding,
                 next_open, intrabar, catastrophe stop, spot long)
    calibrate.py grid search ανά ζεύγος (argmax ή smoothed), evaluate_portfolio, pairs.yaml
    experiments.py  προκαθορισμένες εκδοχές, validation αποφασίζει / test επιβεβαιώνει
  runtime/     online bot
    feed.py      DataFeed: κεριά μέσω REST με συμπλήρωση κενών, bid/ask μέσω WS + REST snapshot
                 κάθε 20s, trade tape, funding history
    engine.py    StrategyEngine: decide (υπό lock) -> παράλληλη εκτέλεση -> settle·
                 restore μετά από restart· funding· live_rows/fit_rows· pause/close controls
    execution.py PairExecutor (taker ή maker_first), PaperVenue (ρεαλιστικά fills από trade tape)
    risk.py      RiskManager (kill switch, max open pairs, όριο έκθεσης ανά νόμισμα, φρέσκες τιμές)
    store.py     SQLite (WAL, schema v3): pair_state, orders (write-ahead), fills, trades,
                 equity, equity_live, pair_marks, pair_live, fit_history, events, kv, commands
    commands.py  CommandProcessor: εντολές από τα κουμπιά του dashboard
    report.py    status/export
    app.py       σύνδεση όλων, ωριαίος κύκλος, stop loop (5s), live loop (15s), command loop (2s)
  config.py    typed config + profiles (strict / hardened / kalman / kalman_ts)
  cli.py       python -m cryptopairs <command>
  dashboard.py σημείο εκκίνησης του dashboard: sidebar + st.navigation (σελίδες στην κορυφή)
  ui/          theme.py (χρώματα, Plotly template, CSS), data.py (read-only queries, KPI, log reader),
               charts.py, lab.py (what-if, backtest σε archive + κεριά Binance ως την τελευταία ώρα),
               widgets.py (Ctx, sidebar με what-if form, KPI, status strip),
               views/ overview, pair, backtest, executions, controls, logs
config/config.yaml   όλες οι ρυθμίσεις
tests/               83 tests (core, backtest με σύγκριση με τον αρχικό κώδικα, funnel,
                     runtime, execution, persistence/crash, dashboard/controls)
data/                klines/1h, funding, meta/chains.csv (197 νομίσματα, 2023-06 .. 2026-09)
runs/                latest (strict), hardened, kalman (K1 = ΤΡΕΧΕΙ), kalman_ts (K2),
                     experiments*, experiments_kalman (ΠΑΡΩΧΗΜΕΝΟ, bug), experiments_kalman_v2
state/               paper.db (τρέχον) + αρχειοθετημένες βάσεις
logs/                paper.log, research.log, experiments.log, ...
notebooks/algorithm_explained.ipynb   εξήγηση του αλγορίθμου στα ελληνικά (OLS, παλιά έκδοση)
legacy/              ο αρχικός κώδικας (για το τεστ σύγκρισης)
Dockerfile, docker-compose.yml (bot + dashboard), deploy.sh, DEPLOY.md (ελληνικός οδηγός VPS), README.md
requirements.txt, requirements-bot.txt, requirements-dashboard.txt, pyproject.toml
```

**Αρχή σχεδιασμού:** ένας πυρήνας (`core/strategy.py`) αποφασίζει **παντού**. Paper και live διαφέρουν μόνο στο `Venue` (PaperVenue έναντι μελλοντικού LiveVenue).

---

## 4. Τρέχουσα ρύθμιση του bot (profile `kalman` = K1)

| Ρύθμιση | Τιμή |
|---|---|
| Εκτιμητής spread | **Kalman filter** (δ ανά ζεύγος ∈ {1e-7, 1e-6, 1e-5}, warm-up/σ 720 h, outlier 4σ, ιστορικό 8.760 h) |
| Μόχλευση | **2x** |
| Stop-loss | 20% του equity εισόδου (στο κλείσιμο ώρας) |
| Stop έκτακτης ανάγκης | **35%**, ελέγχεται συνεχώς (κάθε 5 s) |
| Cooldown μετά από stop | 120 h |
| Απενεργοποίηση | equity ≤ 20% του αρχικού (κανόνας Algorithm 1) |
| Ανώτατο \|Z\| εισόδου | **10** (προστασία από σπασμένες σχέσεις και παγωμένες τιμές) |
| β εντός ορίων | [0,05, 20] |
| Φίλτρο κόστους | edge ≥ 1,5 × κόστος κύκλου |
| Εκτέλεση | **maker_first**: post-only στο μεγαλύτερο σκέλος, κάλυψη κάθε fill με market εντολή, market μετά από 300 s· stops πάντα taker |
| Κόστη (backtest) | taker 5 bps, maker 2 bps (+1 bp αρνητική επιλογή), μισό spread 5 bps, slippage 5 bps, funding ναι, next_open, `maker_legs: 1` |
| Κίνδυνος | max 50 ανοιχτά ζεύγη, ≤ 2x equity ανά νόμισμα, kill switch στο −25% από το peak |
| Κεφάλαιο paper | €100 ανά ζεύγος (17 ενεργά ζεύγη = €1.700· NKN, LRC, HFT παραλείφθηκαν: όχι TRADING) |
| Αξιολόγηση | κάθε ώρα + αμέσως στην εκκίνηση (`evaluate_on_start: true`) |
| Live στιγμιότυπα | κάθε 15 s (διατήρηση 72 h)· εντολές dashboard κάθε 2 s, όριο ηλικίας 120 s |

Profile `kalman_ts` = K2: το ίδιο, συν χρονικό όριο 3 half-lives, με ζεύγη στο `runs/kalman_ts/pairs.yaml`.

---

## 5. Χρονολόγιο: τι έγινε και γιατί

### Φάση 1: Αξιολόγηση
- Ο αρχικός κώδικας ήταν **μόνο έρευνα/backtest**: χωρίς live controller, χωρίς τον κώδικα του funnel.
- **Bugs του αρχικού κώδικα που βρέθηκαν:**
  - `abs(beta)` στο sizing δίνει λάθος αντιστάθμιση όταν β < 0;
  - `require_cointegration=True` ως default, ενώ το paper το αφαιρεί;
  - ADF σε κάθε refit;
  - εκτελέσεις στο close του κεριού του σήματος, χωρίς κόστη/funding/ρευστοποίηση;
  - `fillna(method=)` σε pandas 3;
  - IC στο `output_chain.csv` με παράθυρα 1Y/2Y που δεν υπάρχουν στο paper (πιθανή μεροληψία).
- **Ασάφειες του paper που λύθηκαν:**
  - refit: κάθε κερί όσο είμαστε flat, «παγωμένο» στη θέση;
  - απενεργοποίηση: τελικά ο κανόνας equity floor (δες «μαθήματα»).

### Φάση 2: Πυρήνας, έρευνα, paper runtime
- Νέο πακέτο `cryptopairs/`. Το νέο backtest **αναπαράγει τον αρχικό σε κάθε κερί** (τεστ σύγκρισης).
- Τρέχοντα δεδομένα: 197 νομίσματα (κρατήθηκαν από 860 στο αρχείο), Ιούν. 2023 – Σεπ. 2026.
- Το BAND–NKN, το «καλύτερο» ζεύγος του paper, έχασε **−96%** τον Μάρ.–Σεπ. 2026 με τις παραμέτρους του paper (5x), εξαιτίας ενός pump +28% σε μία ώρα στις 2026-04-17. Αυτό έδειξε τον κίνδυνο κενών τιμής.

### Φάση 3a: Βάση και ανάκτηση
- SQLite με write-ahead εντολές, ατομικές εγγραφές, restore μετά από restart, ζεύγη «orphan» μόνο για κλείσιμο, αναμονή νέων παραμέτρων μέχρι να κλείσει η θέση.
- Εντολές `status`, `export`, `reset-kill-switch`, `paper --fresh`, `paper --name`.

### Έρευνα κόστους και παραλλαγών (όλα στην περίοδο test, εκτός αν σημειώνεται)
- Strict (υποθέσεις paper): **Sharpe 2.61, +48,3%**, MaxDD −12,1%, 1.114 συναλλαγές, 44% stops, 20 απενεργοποιήσεις. Αντίστοιχο με το paper (3.44, +50,7%).
- Το παλιό hardened έβγαλε −4.31. Αιτίες, που είναι **δικές μου επιλογές μοντελοποίησης**:
  - stop μέσα στο κερί με χειρότερη περίπτωση: μόνο του έδωσε −19%;
  - απενεργοποίηση στο 40% drawdown από το peak: απενεργοποίησε 95/100 ζεύγη.
- Ρεαλιστικά κόστη με stop μόνο στο κλείσιμο ώρας:
  - 5x: Sharpe 1.18 (+17%);
  - 2x: 1.53;
  - 2x + catastrophe stop 35%: 2.11 (+20%). Αυτό επιλέχθηκε κοιτώντας το test, άρα είναι snooping.
- Hardened με κανονική βαθμονόμηση: **Sharpe 0.72, +5,6%**, fees €18,9k, **funding €54,3k**.
- Funding:
  - long σε spot: 0.69;
  - φίλτρο «short μόνο με θετικό funding»: 0.21. Το funding έγινε έσοδο, αλλά χάθηκαν οι κερδοφόρες συναλλαγές.
  - `net_cost`: χωρίς επίδραση.
  - Συμπέρασμα: το funding το πληρώνει **κυρίως το short σκέλος**, γιατί τα νομίσματα που κάνουμε short έχουν αρνητικό funding.
- Πείραμα V0–V4 (time stop / stop σε Z / smoothed+όριο ανά νόμισμα): **όλες οι εκδοχές αρνητικές στο validation**. Οι καλές τιμές του test δεν επιβεβαιώθηκαν, οπότε καμία δεν υιοθετήθηκε.
- Διάγνωση του validation (V0):

  | Βήμα | Απόδοση |
  |---|---|
  | Χωρίς κόστη | −0,4% |
  | + fees | −2,1% |
  | + spread/slippage | −4,8% |
  | + funding | **−8,7%** |

  - **Η 10/10/2025** (κραχ ρευστοποιήσεων) **κόστισε −€169k** (~17%).
  - Win rate 65%, αλλά η μέση ζημιά είναι 2,2 φορές το μέσο κέρδος.
  - Μόνο 27/97 ζεύγη κέρδισαν.

### Kalman filter
- Υλοποίηση: Q = δ·R (δ = λόγος σήματος προς θόρυβο, «μνήμη» ≈ 1/√δ). Ισορροπία x̄-centered, Z = επίπεδο spread / σ (όχι το innovation μιας ώρας), αγνόηση outliers, α/β/σ παγωμένα στη θέση.
- **Bug (βρέθηκε από το paper):** το σ κατέρρεε όταν η απόκλιση έμενε μόνιμα από τη μία πλευρά (κομμένες τιμές + std γύρω από τον μέσο όρο). Το ALICE/T έδειξε Z = 37.
  - Fix: σ = **RMS** του spread.
  - Επίσης: σ = NaN όταν είναι ~0, και `max_entry_z` = 10 (υπήρχε περίπτωση LIT με παγωμένη τιμή και Z ≈ 1,5e8).
- **Αποτελέσματα μετά το fix** (`runs/experiments_kalman_v2/summary.txt`):

| Εκδοχή | Validation | Test |
|---|---|---|
| V0 OLS | −0.56 Sharpe, −8,7% | 0.72, +5,6% |
| **K1 Kalman** (επιλογή του κανόνα) | **−0.51, −9,7%** | **2.85, +27,5%** |
| K2 Kalman + time stop | −0.53, −7,1% | 2.50, +23,0% |

Στο validation οι τρεις είναι πρακτικά ίδιες. Στο test το Kalman είναι σαφώς καλύτερο.

### Maker εκτέλεση
- Γρήγορος έλεγχος (έγινε με τις παραμέτρους K2 **πριν** το fix του Kalman, άρα είναι ενδεικτικός):

  | Σενάριο | Validation | Test |
  |---|---|---|
  | Όλα taker | −5,6% | +23,5% |
  | **Ένα σκέλος maker** | **−1,0%** | **+29,4%** |
  | Όλα maker (αισιόδοξο όριο) | −2,3% | +36,3% |

- Υλοποιήθηκε για όλα: backtest (`maker_legs`), PairExecutor `maker_first`, PaperVenue, παράλληλες εκτελέσεις στο engine.
- Πρώτες live εκτελέσεις στο paper: maker **50–69%** της αξίας, fees **~3,2 bps** (taker = 5), διάρκεια 6–100 s. Τα ήσυχα νομίσματα (SFP, SSV) έχουν ελάχιστες συναλλαγές, οπότε συχνά θα καταλήγουν σε fallback σε taker.

### Dashboard και χειρισμός
- Πρώτη έκδοση: equity curve, PnL ανά ζεύγος, πίνακες, execution quality.
- **Real-time έκδοση** (τελευταία δουλειά, ενεργοποιείται με την επανεκκίνηση της εκκρεμότητας #1):
  - μπάρα κατάστασης με heartbeat;
  - Portfolio με live PnL και αποστάσεις από stop;
  - Pair detail: τιμές, spread με ζώνες, Z, Kalman β/σ «μοντέλο τώρα» μαζί με «σε χρήση», κάρτα θέσης;
  - Executions;
  - Controls: pause/resume ζεύγους ή όλων, close pair, reset kill switch, flatten all, όλα με επιβεβαίωση.
- Paper funding: το bot χρεώνει πλέον το **πραγματικό** funding της Binance (`settle_funding`).
- **Dashboard v3** (ζήτησε «σαν σελίδα», real time, με επιλογές· **όχι mock data**):
  - `st.navigation(position="top")` με 6 σελίδες, σκούρο θέμα, ανανέωση με fragments (2–30 s).
  - Sidebar: bot, focus pair, φίλτρο ζευγών, παράθυρο ιστορικού, ανανέωση, και φόρμα what-if (Z entry/exit, δ ή lookback, σ-window, κεφάλαιο, μόχλευση) με «Recalculate».
  - Overview: 8 KPI (Bot, Equity με sparkline, Daily PnL, Open, Sharpe, Max DD, Z του focus pair, Status), equity/drawdown, PnL ανά ζεύγος, πίνακας θέσεων (τιμές εισόδου/τώρα, notional, spread, Z, απόσταση από exit/stop), signal monitor, έκθεση ανά νόμισμα.
  - Pair monitor: Chart A (κανονικοποιημένες τιμές), Chart B (Z με τα όρια σε χρήση και τα what-if, markers BUY/SELL/CLOSE/STOP των πραγματικών συναλλαγών, κούφια markers what-if), διαγνωστικά μοντέλου, σκέλη θέσης, πίνακας παραμέτρων «calibrated / in use / what-if», **Apply to live bot** (με επιβεβαίωση) και Revert.
  - Backtest lab: ίδιο `run_backtest` με τα κόστη του profile, πάνω στο archive + κεριά/funding από τη Binance (ccxt) ως την τελευταία κλειστή ώρα. Calibrated vs what-if, χάρτης Sharpe (z_entry × z_exit), συναλλαγές, CSV. Σήμανση IN-SAMPLE όταν η περίοδος είναι πριν από 2026-03-01.
  - System logs: tail του `logs/<bot>.log` με φίλτρα (level, source, κείμενο) και το event journal.
  - Bot: `StrategyEngine.set_params`: σε flat ζεύγος αλλάζει αμέσως (με refit αν άλλαξε δ/lookback)· σε θέση περιμένει να κλείσει. Αποθηκεύεται στο kv `param_overrides` {params, base}, ισχύει μετά από restart και **ακυρώνεται όταν αλλάξει η βαθμονόμηση στο pairs.yaml**. Αλλαγή OLS↔Kalman και `z_entry ≥ max_entry_z` απορρίπτονται. Κεφάλαιο και μόχλευση είναι μόνο what-if.
  - Έλεγχος σε πραγματικά δεδομένα: MAGIC/CTSI out-of-sample (Μάρ.–Οκτ. 2026) +14,3%, Sharpe 0,74, **MaxDD −53%** (catastrophe stop −35% στις 2/4 και stop −21% στις 15/4). Η ανοιχτή SHORT του backtest (27/9) ταιριάζει με του paper bot.

---

## 6. Τρέχουσα κατάσταση του paper bot

- Συνεδρία `paper_20261001_165022_9c1f9f` (profile `kalman`), ξεκίνησε με `--fresh` ~19:50 ώρα Ελλάδας στις 2026-10-01.
- 3 ανοιχτές θέσεις: **SFPUSDT_SSVUSDT (SHORT)**, **MAGICUSDT_CTSIUSDT (SHORT)**, **TUSDT_GRTUSDT (LONG)**. Equity ≈ €1.700,05.
- Αρχειοθετημένες βάσεις:
  - `state/paper_20261001_154256.db`: το παλιό V0;
  - `state/paper_20261001_165022.db`: η συνεδρία K2 με το bug του Kalman (6 θέσεις, 2 από αυτές λάθος).

---

## 7. Μαθήματα και bugs (για να μην επαναληφθούν)

1. **Απενεργοποίηση στο 20% peak drawdown με stop-loss 20%:** κάθε stop απενεργοποιεί το ζεύγος. Το όριο πρέπει να είναι πάνω από το stop (υπάρχει έλεγχος στο config validation). Το paper έτρεξε στην πράξη τον κανόνα equity floor.
2. **Stop μέσα στο κερί με χειρότερη περίπτωση** (και τα δύο σκέλη στο χειρότερο σημείο ταυτόχρονα): υπερβολικά απαισιόδοξο. Τώρα η έξοδος γίνεται στο σημείο που η καμπύλη «περνά» το όριο.
3. **Ήσυχα νομίσματα:** το WS στέλνει τιμή μόνο όταν αλλάζει. Γι' αυτό προστέθηκε REST snapshot όλων των τιμών κάθε 20 s, και η τιμή θεωρείται «παλιά» μετά από 45 s.
4. **Σύγκρουση ονόματος (`kind`) στο `store.event(**stats)`:** το σφάλμα σταματούσε την εφαρμογή της εκτέλεσης. Τα στατιστικά γράφονται πλέον μετά το commit, μέσα σε try.
5. **Σ του Kalman:** RMS, όχι std γύρω από τον μέσο όρο (§5). Τεστ: `test_kalman_sigma_does_not_collapse_on_a_persistent_divergence`.
6. **Παγωμένες τιμές / delisting:** σ = NaN και `max_entry_z`.
7. **Smoothed επιλογή:** ο μέσος όρος γειτόνων ευνοεί τα spikes, οπότε χρησιμοποιείται **διάμεσος**.
8. **Data snooping:** κάθε νέα ιδέα αξιολογείται **μόνο** με το `experiments.py` (απόφαση στο validation). Το 2.11 του hardened ήταν snooping.
9. **pandas 3:** οι μονάδες των datetime ποικίλλουν (`.dt.as_unit("ms")`)· `Timestamp.value` είναι πάντα ns.
10. **Streamlit fragments:** δεν γράφουν σε containers ή στο sidebar που φτιάχτηκαν έξω από αυτά. Γι' αυτό το sidebar είναι εκτός fragment, και κάθε σελίδα έχει το δικό της fragment.
11. **Streamlit θέμα:** το `.streamlit/config.toml` διαβάζεται από τον φάκελο εκκίνησης και μόνο στην εκκίνηση. Το `cli.py` ξεκινά το Streamlit από τη ρίζα του project με απόλυτες διαδρομές.
13. **Live ενημέρωση χωρίς τρεμόπαιγμα:**
    - Το `st.plotly_chart` ξαναχτίζει το γράφημα σε κάθε αλλαγή δεδομένων, γιατί το id του περιλαμβάνει το figure, και χάνει το zoom. Στις σελίδες που ανανεώνονται χρησιμοποιείται το `ui/live.py`: component v2 με σταθερό key που κάνει `Plotly.react` με `uirevision`. Επαληθεύτηκε σε headless Firefox: ίδιο DOM node, νέα δεδομένα, το zoom μένει.
    - Οι πίνακες θέλουν `key=`, γιατί αλλιώς ξαναδημιουργούνται και χάνουν το scroll.
    - Το CSS καταργεί το fade των στοιχείων που ξαναϋπολογίζονται (`[data-stale]`) και την ένδειξη «Running».
    - Το παλαιότερο live ιστορικό αραιώνεται σε 1 σημείο/λεπτό (`data.thinned`).
16. **«Κολλημένα» δεδομένα (23:00):** με το bot να τρέχει, μια φρέσκια καρτέλα ενημερωνόταν κανονικά. Ο χρήστης έβλεπε «κολλημένο», για τρεις λόγους:
    - στο διάγραμμα 7 ημερών οι αλλαγές ανά 5 s είναι αόρατες;
    - μια σελίδα που είχε χάσει τη σύνδεση δεν το έδειχνε, γιατί είχα κρύψει το status widget;
    - μετά από αλλαγές κώδικα, το runOnSave ξαναφόρτωνε τις σελίδες αλλά όχι τα modules, και οι σελίδες έβγαζαν σφάλματα.
    Διορθώσεις:
    - **watchdog badge** (πάνω δεξιά), που τρέχει στον browser κάθε 1 s: δείχνει «LIVE · prices Xs old · page Ys ago», γίνεται κόκκινο αν χαθεί η σύνδεση και κάνει αυτόματο reload στα ~45 s;
    - **live γραφήματα 60 λεπτών** (Overview: equity, Pair monitor: τιμές % και Z), που κόβουν τη γραμμή στα κενά (`charts._with_gaps`);
    - το `dashboard.py` κάνει **reload τα ui modules** όταν αλλάζουν τα αρχεία τους.
    Επαληθεύτηκε στο dashboard του χρήστη (8501) με headless Firefox: 5 σελίδες χωρίς σφάλματα, οι τιμές αλλάζουν, τα γραφήματα μένουν στη θέση τους. Επίσης, το σενάριο restart του server δείχνει κόκκινο badge και μετά επαναφορά.
14. **Έλεγχος εμφάνισης:** υπάρχει headless Firefox μέσω Marionette (script στο scratchpad, ξεχωριστό προφίλ). Το Streamlit κάνει scroll στο `stMain` και όχι στο document. Μην κάνεις `pkill -f` με μοτίβο που υπάρχει στην ίδια εντολή bash, γιατί σκοτώνει το shell (exit 144).
15. **Εμφάνιση dashboard:** οι κάρτες KPI είναι CSS grid (4 ανά σειρά, 8 από 1820px πλάτος), με `st.container(horizontal=True, key="kpi_*")`. Οι άξονες Plotly έχουν `automargin`. Το `toolbarMode = "viewer"` κρύβει Deploy και τα αναδυόμενα του Streamlit. Οι πίνακες έχουν `placeholder=""` (όχι «None»). Ο άξονας Z περιλαμβάνει και το live Z.
17. **Η Binance χώρισε τα WebSocket streams των futures** (εντοπίστηκε 2026-10-02): το `/public` δίνει depth και bookTicker, το `/market` δίνει aggTrade, kline και markPrice. Το παλιό `wss://fstream.binance.com/stream` δίνει **μόνο** depth και bookTicker. Το ccxt.pro του bot δεν επηρεάζεται (943 trades σε 10 s στον έλεγχο), αλλά ο browser ανοίγει δύο συνδέσεις (`ui/js/common.js`).
18. **Χρόνοι συναλλαγών:** οι αποφάσεις στο ωριαίο κλείσιμο σφραγίζονται με την ώρα **ανοίγματος** του κεριού (`Intent.ts = bar_ts`), ενώ τα stops ανάμεσα στα κεριά και οι χειροκίνητες εντολές έχουν την πραγματική ώρα. Το dashboard κανονικοποιεί με `ui.data.event_ms` (+1 h όταν ο χρόνος πέφτει ακριβώς στην ώρα).
19. **`st.segmented_control`:** χωρίς `required=True`, ένα δεύτερο κλικ στην ενεργή επιλογή την αποεπιλέγει.
20. **Lightweight Charts:** ο άξονας χρόνου είναι κατά δείκτη (κάθε μοναδικός χρόνος = ένα «bar»), οπότε κάθε γράφημα χρησιμοποιεί **μία** ανάλυση. Τα markers μπαίνουν σε χρόνο που υπάρχει στη σειρά (`tvcharts._snap`). Τα components v2 του Streamlit καλούν το JS σε κάθε rerun και το cleanup μόνο στο unmount, οπότε η κατάσταση κρατιέται στο `parentElement`.
21. **Ώρα:** η βάση και το bot δουλεύουν σε UTC, και το dashboard δείχνει Αθήνα μέσω του `ui.data.to_time` (μην ξαναγράψεις `pd.to_datetime(..., unit="ms")` σε σελίδα). Το Lightweight Charts δεν ξέρει ζώνες ώρας: χρησιμοποιούνται formatters με `Intl` (`cpTimeFmt`), όχι μετατόπιση των δεδομένων.
22. **Fragment στο sidebar:** καλείται μέσα σε `with st.sidebar:`. Ένα κλικ στη watchlist γράφει `_focus_pick` και κάνει `st.rerun()`, και το sidebar το εφαρμόζει στο `focus` **πριν** δημιουργηθεί το selectbox. Ένα widget δεν αλλάζει αφού έχει δημιουργηθεί στην ίδια εκτέλεση.
23. **Φόρμες με sliders όχι μέσα σε fragment που ανανεώνεται** (what-if): μπορεί να χαθούν οι τιμές που δεν έχουν υποβληθεί. Η φόρμα μένει σε επίπεδο σελίδας και κάνει `st.rerun()` στο Recalculate.
21. **Rewind και αρχεία:** το rewind του Claude Code επαναφέρει μόνο αρχεία που άλλαξαν με Write/Edit. Οι αλλαγές από scripts στο Bash μένουν, και ο κώδικας γίνεται μισός-μισός. Μετά από rewind, πριν από deploy: `python -m pytest -q`, και να μην υπάρχουν imports σε modules που λείπουν.
12. **`select_slider`:** αν η τρέχουσα τιμή δεν είναι στις επιλογές, «κουμπώνει» στην πλησιέστερη. Τότε ένα «Recalculate» χωρίς αλλαγή θα άλλαζε την παράμετρο, γι' αυτό η τρέχουσα τιμή μπαίνει πάντα στις επιλογές.

---

## 8. Συνηθισμένες εντολές

```bash
cd ~/Documents/CryptoPairsBot && source .venv/bin/activate

# paper bot (K1). Χωρίς --fresh = συνέχεια με τις θέσεις· --fresh = νέα αρχή (αρχειοθέτηση)
python -m cryptopairs --profile kalman paper     # pairs: runs/kalman/pairs.yaml (αυτόματα από το profile)
python -m cryptopairs dashboard                 # http://localhost:8501
python -m cryptopairs status                    # σύνοψη στο terminal
python -m cryptopairs export                    # CSV στο exports/
tail -f logs/paper.log

# έρευνα
python -m cryptopairs download                  # ενημέρωση δεδομένων (incremental)
python -m cryptopairs metadata                  # CoinGecko chains
python -m cryptopairs --profile hardened research --tag <tag> --top 20
python -m cryptopairs --profile hardened experiments --only V0 K1 K2 --out runs/<φάκελος>
python -m cryptopairs backtest BANDUSDT NKNUSDT --lookback 4320 --z-entry 1.0 --z-exit 0.0

python -m pytest -q                             # 111 tests
```

Επανεκκίνηση του bot χρειάζεται μετά από **κάθε** αλλαγή κώδικα ή ρυθμίσεων (χωρίς `--fresh`, εκτός αν αλλάζει το μοντέλο ή οι θέσεις είναι λάθος). Το dashboard ξαναφορτώνεται μόνο του (`runOnSave`).

---

## 9. Σχέδιο για το live (Φάση 3b, δεν έχει ξεκινήσει)

- `LiveVenue` στο `runtime/execution.py`, με το ίδιο interface `Venue`:
  - ccxt `create_order` με post-only (GTX) για maker και market για taker;
  - `clientOrderId` = το `client_id` που ήδη γράφει το `store.order_pending`.
- **Ανάκτηση:** στην εκκίνηση, οι `pending` εντολές συμφωνούνται με το ανταλλακτήριο μέσω client id, αντί να σημειώνονται «abandoned» όπως στο paper. Οι θέσεις συμφωνούνται με το άθροισμα των θέσεων ανά ζεύγος.
- Εφεδρικά reduce-only STOP_MARKET στο ανταλλακτήριο, ώστε η προστασία να ισχύει και όταν το bot είναι κάτω.
- Μερικές εκτελέσεις και ανισορροπία σκελών: υπάρχει το event `execution_imbalance`, πρέπει να υλοποιηθεί αυτόματη διόρθωση.
- Rate limits από τα `exchangeInfo.rateLimits`. Στρογγυλοποίηση σε lots με το `round_to_lots`, που υπάρχει ήδη.
- Cross margin, μόχλευση ανά σύμβολο, API keys από `.env`. **Πρώτα Binance futures testnet.**
- Live funding από το income history. Πραγματικά fees από `fetch_trading_fees`.
- Πριν από live: νέα δεδομένα και ξαναβαθμονόμηση· συμφωνία των αποτελεσμάτων του paper με το backtest.

---

## 10. Προτιμήσεις του χρήστη

- Ελληνικά. Πρακτικές εξηγήσεις με πίνακες· οδηγίες εντολών βήμα-βήμα.
- Τρέχει μόνος του τις μεγάλες εντολές στο terminal του. **Μη ξεκινάς μεγάλες εκτελέσεις ή live smoke tests χωρίς να ρωτήσεις** (σύντομες αναλύσεις κάτω από ~1 λεπτό είναι εντάξει). Τα πειράματα στο παρασκήνιο έχουν γίνει με την έγκρισή του.
- Όταν λέει «φτιάξ' το», θέλει υλοποίηση, όχι άλλη ανάλυση. Θέλει να βλέπει τα πάντα σαν «fund».
- Θέλει να του λέμε καθαρά πότε χρειάζεται επανεκκίνηση και πότε `--fresh`.
- Ειλικρίνεια για τα αποτελέσματα: έχει δεχτεί ότι η στρατηγική δεν είναι ακόμα για live.
