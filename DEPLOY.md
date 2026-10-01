# Εγκατάσταση του bot σε server (VPS)

Το bot τρέχει σε **Docker** σε έναν μικρό server που νοικιάζεις. Δουλεύει 24/7 χωρίς τον υπολογιστή σου, ξεκινά αυτόματα αν πέσει ή αν γίνει επανεκκίνηση του server, και κρατάει τη βάση του (`state/paper.db`).

**Τι χρειάζεσαι:** έναν VPS με Ubuntu 24.04, 1 πυρήνα και 1–2 GB RAM (π.χ. Hetzner Cloud, ο φθηνότερος, ~€4–5/μήνα).
**Τοποθεσία:** Ευρώπη (Γερμανία ή Φινλανδία). **Όχι ΗΠΑ**, γιατί η Binance μπλοκάρει IP από ΗΠΑ.

---

## 1. Κλειδί SSH (μία φορά, στον υπολογιστή σου)

```bash
ssh-keygen -t ed25519          # Enter σε όλες τις ερωτήσεις
cat ~/.ssh/id_ed25519.pub      # αντέγραψε αυτή τη γραμμή
```

## 2. Δημιουργία server (π.χ. Hetzner Cloud)

1. Λογαριασμός στο hetzner.com → **Cloud** → **New project** → **Add server**.
2. **Location:** Falkenstein / Nuremberg / Helsinki.
3. **Image:** Ubuntu 24.04.
4. **Type:** ο φθηνότερος (shared vCPU, x86).
5. **SSH key:** επικόλλησε τη γραμμή από το βήμα 1.
6. **Create.** Σημείωσε τη διεύθυνση **IP** του server.

Στα παρακάτω, όπου γράφει `IP`, βάλε αυτή τη διεύθυνση.

## 3. Προετοιμασία του server

```bash
ssh root@IP
```

Μέσα στον server:

```bash
apt update && apt -y upgrade
curl -fsSL https://get.docker.com | sh
mkdir -p /opt/cryptopairs
curl -s https://fapi.binance.com/fapi/v1/time   # πρέπει να δείξει {"serverTime":...}
exit
```

## 4. Αντιγραφή του bot (από τον υπολογιστή σου)

```bash
cd ~/Documents/CryptoPairsBot
rsync -avR --exclude __pycache__ cryptopairs config runs/kalman \
      Dockerfile docker-compose.yml requirements-bot.txt .dockerignore root@IP:/opt/cryptopairs/
```

## 5. Εκκίνηση

```bash
ssh root@IP
cd /opt/cryptopairs
mkdir -p state logs && chown -R 1000:1000 state logs
docker compose up -d --build
```

Το πρώτο build θέλει 2–3 λεπτά. Μετά το bot τρέχει στο παρασκήνιο, **και μπορείς να κλείσεις τον υπολογιστή σου**.

## 6. Παρακολούθηση

Συνδέεσαι με `ssh root@IP` και μετά `cd /opt/cryptopairs`:

| Τι θέλεις | Εντολή |
|---|---|
| Σύνοψη (θέσεις, συναλλαγές, κέρδος) | `docker compose exec bot python -m cryptopairs status` |
| Ζωντανή ροή (Ctrl+C για έξοδο, το bot συνεχίζει) | `docker compose logs -f --tail 50` |
| Αν τρέχει | `docker compose ps` |
| Εξαγωγή σε CSV | `docker compose exec bot python -m cryptopairs export --out logs/export` |

Για να φέρεις τη βάση στον υπολογιστή σου (π.χ. για το notebook):

```bash
rsync -av root@IP:/opt/cryptopairs/state/ ~/Documents/CryptoPairsBot/state/
rsync -av root@IP:/opt/cryptopairs/logs/ ~/Documents/CryptoPairsBot/logs/
```

## 7. Ενημέρωση κώδικα ή ζευγών

Ξανατρέχεις το βήμα 4 από τον υπολογιστή σου και μετά, στον server:

```bash
cd /opt/cryptopairs && docker compose up -d --build
```

Η βάση **δεν** χάνεται: το bot συνεχίζει με τις ανοιχτές θέσεις του.

## 8. Διακοπή και νέα αρχή

```bash
docker compose stop                       # σταματά (κρατά τη βάση)
docker compose start                      # ξαναξεκινά από εκεί που ήταν
docker compose stop && mv state/paper.db state/paper_old.db && docker compose start   # μηδενισμός
```
