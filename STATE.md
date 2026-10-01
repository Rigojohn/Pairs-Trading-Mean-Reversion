# CryptoPairsBot: κατάσταση project (handoff)

> Τελευταία ενημέρωση: **2026-10-01, ~21:45 ώρα Ελλάδας**. Γράφτηκε για να συνεχίσει ένα νέο session από εδώ.
> Γλώσσα επικοινωνίας με τον χρήστη: **ελληνικά** (κώδικας και τεχνικοί όροι στα αγγλικά).

---

## 0. TL;DR

- **Στόχος:** bot για pairs trading σε Binance USDⓈ-M perpetuals, βασισμένο στο paper των Stoikov et al., *Pairs Trading in Crypto* (`ssrn-6188418.pdf`). Τρέχει πρώτα σε **paper trading** και μετά σε **live**.
- **Τι υπάρχει:** πλήρες σύστημα σε Python, χτισμένο από την αρχή πάνω στον αρχικό κώδικα (`CryptoPairTrading-main.zip`, φυλάσσεται στο `legacy/`):
  - έρευνα: λήψη δεδομένων, funnel, βαθμονόμηση, backtest με ρεαλιστικά κόστη, πειράματα validation/test;
  - paper bot: Kalman filter, εκτέλεση «maker πρώτα», κίνδυνος, βάση SQLite με ανάκτηση μετά από crash;
  - real-time dashboard **πολλών σελίδων** (σκούρο θέμα, what-if sliders, Backtest lab, εφαρμογή παραμέτρων στο bot);
  - Docker για VPS (το build δεν έχει δοκιμαστεί);
  - **81 tests περνούν**.
- **Βασικό εύρημα:** με ρεαλιστικά κόστη η στρατηγική έχει **μικρό και ασταθές πλεονέκτημα**. Η καλύτερη εκδοχή (K1 Kalman) έβγαλε **−9,7% στο validation** (Αύγ. 2025 – Φεβ. 2026) και **+27,5% στο test** (Μάρ. – Σεπ. 2026). **Δεν έχει προταθεί live με πραγματικά χρήματα.** Το paper trading είναι η τρέχουσα «αληθινή» δοκιμή.
- **Τι τρέχει τώρα (τοπικά):**
  - paper bot με profile `kalman` (K1), 3 ανοιχτές θέσεις, equity ≈ €1.700;
  - dashboard στο http://localhost:8501.
  - Και τα δύο επανεκκινήθηκαν στις 20:51–20:52 (χωρίς `--fresh`) με το schema v3. Οι θέσεις διατηρήθηκαν.
  - **Το νέο dashboard (v3, σελίδες) γράφτηκε μετά.** Θέλει ξανά επανεκκίνηση και των δύο, χωρίς `--fresh`. Δες §1 #0.

---

## 1. Εκκρεμότητες (με σειρά προτεραιότητας)

0. **Επανεκκίνηση bot ΚΑΙ dashboard, χωρίς `--fresh`** (μετά το dashboard v3, 2026-10-01 ~21:45):
   - το bot χρειάζεται τις νέες εντολές `set_params` / `clear_params`· ένα παλιό bot τις απορρίπτει ως «unknown command»;
   - το dashboard διαβάζει το σκούρο θέμα (`.streamlit/config.toml`) μόνο στην εκκίνηση, και πρέπει να ξεκινά μέσω `python -m cryptopairs dashboard`.
   - Έλεγχος: μπάρα πλοήγησης πάνω (Overview, Pair monitor, Backtest lab, Executions, Risk & controls, System logs), σκούρο φόντο.
1. ~~Επανεκκίνηση bot και dashboard~~: **ΕΓΙΝΕ** 2026-10-01 20:51. Schema v3 ενεργό, `fit_history` = 12.240 γραμμές (30 μέρες × 24 h × 17 ζεύγη), `pair_live` γράφεται κάθε 15 s, 3 θέσεις αποκαταστάθηκαν. Νέα συνεδρία: `paper_20261001_175113_619416`.
   - **Μένει:** δοκιμή ενός κουμπιού (π.χ. «Pause entries for this pair»), που πρέπει να δώσει `done` σε ~2 s. Ο πίνακας `commands` είναι ακόμα άδειος.
2. **Paper trading για 2–4 εβδομάδες.** Συγκρίνουμε:
   - με το backtest;
   - ποσοστό maker και κόστος σε bps (καρτέλα Executions);
   - funding.
3. **Νέα δεδομένα και ξαναβαθμονόμηση πριν από οποιοδήποτε live.** Τα ζεύγη του `runs/kalman/pairs.yaml` βαθμονομήθηκαν με δεδομένα **μέχρι 2026-02-28**, και τα τοπικά δεδομένα φτάνουν ως **2026-09-30**.
4. **Φάση 3b: live εκτέλεση** (δες §9). Δεν έχει ξεκινήσει.
5. **Docker/VPS.** Το `Dockerfile` και το `docker-compose.yml` υπάρχουν αλλά **δεν έχουν δοκιμαστεί**, γιατί δεν υπάρχει Docker στον υπολογιστή του χρήστη. Το compose **δεν περιλαμβάνει ακόμα το dashboard** (χρειάζεται service με port στο 127.0.0.1 και πρόσβαση μέσω SSH tunnel) ούτε το `requirements-dashboard.txt`.
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
- Tests: `python -m pytest -q` (~15 δευτερόλεπτα, **81 passed**).
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
tests/               81 tests (core, backtest με σύγκριση με τον αρχικό κώδικα, funnel,
                     runtime, execution, persistence/crash, dashboard/controls)
data/                klines/1h, funding, meta/chains.csv (197 νομίσματα, 2023-06 .. 2026-09)
runs/                latest (strict), hardened, kalman (K1 = ΤΡΕΧΕΙ), kalman_ts (K2),
                     experiments*, experiments_kalman (ΠΑΡΩΧΗΜΕΝΟ, bug), experiments_kalman_v2
state/               paper.db (τρέχον) + αρχειοθετημένες βάσεις
logs/                paper.log, research.log, experiments.log, ...
notebooks/algorithm_explained.ipynb   εξήγηση του αλγορίθμου στα ελληνικά (OLS, παλιά έκδοση)
legacy/              ο αρχικός κώδικας (για το τεστ σύγκρισης)
Dockerfile, docker-compose.yml, DEPLOY.md (ελληνικός οδηγός VPS), README.md
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
12. **`select_slider`:** αν η τρέχουσα τιμή δεν είναι στις επιλογές, «κουμπώνει» στην πλησιέστερη. Τότε ένα «Recalculate» χωρίς αλλαγή θα άλλαζε την παράμετρο, γι' αυτό η τρέχουσα τιμή μπαίνει πάντα στις επιλογές.

---

## 8. Συνηθισμένες εντολές

```bash
cd ~/Documents/CryptoPairsBot && source .venv/bin/activate

# paper bot (K1). Χωρίς --fresh = συνέχεια με τις θέσεις· --fresh = νέα αρχή (αρχειοθέτηση)
python -m cryptopairs --profile kalman paper --pairs runs/kalman/pairs.yaml
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

python -m pytest -q                             # 81 tests
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
