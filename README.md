# CryptoPairsBot

Bot για pairs trading σε Binance USDⓈ-M perpetuals, βασισμένο στο paper των Stoikov et al., *Pairs Trading in Crypto* (`ssrn-6188418.pdf`).

## Δομή

| Φάκελος | Περιεχόμενο |
|---|---|
| `cryptopairs/core/` | Κοινός πυρήνας για backtest, paper και live: παλινδρόμηση και Z-score, μέγεθος θέσης, μηχανή καταστάσεων ανά ζεύγος, λογιστική |
| `cryptopairs/research/` | Λήψη δεδομένων, funnel επιλογής ζευγών (φίλτρα 1–4), βαθμονόμηση, backtest |
| `cryptopairs/runtime/` | `DataFeed` → `StrategyEngine` → `RiskManager` → `ExecutionEngine` (paper) |
| `cryptopairs/dashboard.py`, `cryptopairs/ui/` | Real-time dashboard (Streamlit, σκούρο θέμα στο `.streamlit/config.toml`): Overview, Pair monitor, Backtest lab, Executions, Risk & controls, System logs |
| `config/config.yaml` | Όλες οι ρυθμίσεις. Το profile `strict` ακολουθεί το paper, το `hardened` προσθέτει τις προσαρμογές για crypto |
| `legacy/` | Ο αρχικός κώδικας, μόνο για σύγκριση στα tests |

## Εντολές

```bash
source .venv/bin/activate
python -m pytest -q                                  # tests (~5 δευτερόλεπτα)

python -m cryptopairs download                       # όλο το αρχείο της Binance (~20–40 λεπτά, κυρίως δίκτυο)
python -m cryptopairs metadata                       # blockchain κάθε νομίσματος (CoinGecko), για το φίλτρο 2
python -m cryptopairs research --top 20              # funnel + βαθμονόμηση + test → runs/latest/
python -m cryptopairs --profile hardened research --tag hardened   # ίδια ροή με ρεαλιστικά κόστη

python -m cryptopairs backtest BANDUSDT NKNUSDT --lookback 4320 --z-entry 1.0 --z-exit 0.0
python -m cryptopairs paper --pairs runs/latest/pairs.yaml          # paper trading σε live δεδομένα
python -m cryptopairs dashboard                                     # http://localhost:8501
```

Στο paper trading δεν στέλνεται καμία εντολή στο ανταλλακτήριο και δεν χρειάζονται API keys. Οι συναλλαγές και το equity γράφονται στον φάκελο `logs/`.
