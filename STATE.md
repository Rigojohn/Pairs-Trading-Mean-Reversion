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

## 1. Εκκρεμότητες (με σειρά προτεραιότητας)

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
