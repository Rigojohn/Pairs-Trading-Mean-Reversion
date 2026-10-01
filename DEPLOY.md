# Εγκατάσταση του bot και του dashboard σε server (VPS)

Το bot και το dashboard τρέχουν σε **Docker** σε έναν μικρό server που νοικιάζεις:

- δουλεύουν 24/7, **χωρίς τον υπολογιστή σου**;
- ξεκινούν αυτόματα αν πέσουν ή αν γίνει επανεκκίνηση του server;
- η βάση του bot (`state/paper.db`) μένει στον server και δεν χάνεται στις ενημερώσεις.

Το dashboard **δεν** ανοίγει δημόσια στο internet, γιατί δεν έχει κωδικό και τα κουμπιά του ελέγχουν το bot. Το βλέπεις από τον υπολογιστή σου μέσω SSH (βήμα 8).

**Τι χρειάζεσαι:** έναν VPS με Ubuntu 24.04, **τουλάχιστον 2 GB RAM** (bot και dashboard μαζί θέλουν ~300 MB) και 20 GB δίσκο. Ένας μήνας paper trading γράφει ~30 MB στη βάση. Προτείνεται το **OVHcloud VPS-1** (2 vCores, 4 GB RAM, 40 GB NVMe) με μηνιαία χρέωση χωρίς δέσμευση.
**Τοποθεσία:** Ευρώπη (Γαλλία ή Γερμανία). **Όχι ΗΠΑ**, γιατί η Binance μπλοκάρει IP από ΗΠΑ. Ούτε Ηνωμένο Βασίλειο.

---

## 1. Κλειδί SSH (μία φορά, στον υπολογιστή σου)

```bash
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519   # αν δεν υπάρχει: Enter σε όλες τις ερωτήσεις
cat ~/.ssh/id_ed25519.pub                           # αντέγραψε αυτή τη γραμμή
```

## 2. Δημιουργία server (OVHcloud VPS-1)

1. https://www.ovhcloud.com/en-ie/vps/ → **VPS-1** → **Configure**.
2. **Location:** Ευρώπη (Γαλλία ή Γερμανία), όποια δεν έχει επιπλέον χρέωση.
3. **Image:** Ubuntu 24.04. Χωρίς επιπλέον εφαρμογές.
4. **SSH key:** αν σου το ζητήσει, επικόλλησε τη γραμμή από το βήμα 1.
5. **Commitment / billing:** **μηνιαία, χωρίς δέσμευση.**
6. Ολοκλήρωσε την παραγγελία. Σε λίγα λεπτά έρχεται email με τη διεύθυνση **IP** του server και τον χρήστη `ubuntu`.

Την **πρώτη** φορά η OVH σε υποχρεώνει να αλλάξεις τον προσωρινό κωδικό, που βρίσκεται στο link του email. Αυτό γίνεται μόνο από εσένα, σε κανονικό terminal:

```bash
ssh ubuntu@IP     # Current password: ο προσωρινός, μετά δύο φορές ένας δικός σου νέος
```

Αν δεν έβαλες SSH key στην παραγγελία, κάνε **Reinstall my VPS** (τρεις τελείες δίπλα στο **OS/Distribution**) με Ubuntu 24.04, κλειδί τύπου **ED25519**, και επικόλλησε τη γραμμή από το βήμα 1.

## 3. Συντόμευση `cryptobot` (μία φορά, στον υπολογιστή σου)

Βάλε την IP του server στη θέση του `IP` και τρέξε:

```bash
cat >> ~/.ssh/config <<'EOF'

Host cryptobot
    HostName IP
    User ubuntu
EOF
```

Από εδώ και πέρα γράφεις `ssh cryptobot` αντί για `ssh ubuntu@IP`.

## 4. Προετοιμασία του server (μία φορά)

```bash
ssh cryptobot
```

Μέσα στον server:

```bash
sudo apt update && sudo apt -y upgrade
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu                       # docker χωρίς sudo
sudo mkdir -p /opt/cryptopairs/state && sudo chown -R ubuntu:ubuntu /opt/cryptopairs
# μόνο με κλειδί, όχι με κωδικό:
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' | sudo tee /etc/ssh/sshd_config.d/00-cryptobot-keys-only.conf && sudo systemctl reload ssh
curl -s https://fapi.binance.com/fapi/v1/time   # πρέπει να δείξει {"serverTime":...}
exit                                                 # ή sudo reboot, αν γράφει *** System restart required ***
```

## 5. Μεταφορά της τρέχουσας paper συνεδρίας (προαιρετικό)

Κάνε αυτό το βήμα αν θέλεις ο server να συνεχίσει από εκεί που είναι τώρα το bot στον υπολογιστή σου, με τις ανοιχτές θέσεις και το ιστορικό. Αλλιώς ο server ξεκινά νέα συνεδρία από το μηδέν.

1. **Σταμάτα το bot στον υπολογιστή σου.** Πάτα Ctrl+C στο terminal του, ή τρέξε `pkill -f "cryptopairs .*paper"`. Περίμενε να γράψει `state saved` στο `logs/paper.log`. Σταμάτα και το τοπικό dashboard, που θα έδειχνε πια παλιά δεδομένα.
2. Αντέγραψε τη βάση:
   ```bash
   cd ~/Documents/CryptoPairsBot
   rsync -av state/paper.db* cryptobot:/opt/cryptopairs/state/
   ```

> **Ποτέ το ίδιο bot σε δύο μέρη ταυτόχρονα.** Από τη στιγμή που τρέχει στον server, μην το ξεκινάς στον υπολογιστή σου.

## 6. Δεδομένα για το Backtest lab (προαιρετικό)

Το Backtest lab χρειάζεται το ιστορικό των τιμών, που είναι 264 MB και ανεβαίνει σε μερικά λεπτά:

```bash
cd ~/Documents/CryptoPairsBot
rsync -av data/ cryptobot:/opt/cryptopairs/data/
```

Χωρίς αυτά οι υπόλοιπες σελίδες δουλεύουν κανονικά. Το Backtest lab γράφει `no local data`.

## 7. Εκκίνηση

```bash
cd ~/Documents/CryptoPairsBot
./deploy.sh
```

Το script αντιγράφει τον κώδικα στον server και ξεκινά το bot και το dashboard. Το πρώτο build θέλει 3–5 λεπτά. Στο τέλος δείχνει δύο containers (`cryptopairs` και `cryptopairs-dashboard`) με κατάσταση `Up`.

**Από εδώ και πέρα μπορείς να κλείσεις τον υπολογιστή σου.**

## 8. Το dashboard από τον υπολογιστή σου

```bash
ssh -N -L 8502:127.0.0.1:8501 cryptobot
```

Μετά άνοιξε στον browser το **http://localhost:8502**.

- Το terminal μένει ανοιχτό χωρίς να γράφει τίποτα. Αυτό είναι φυσιολογικό: όσο είναι ανοιχτό, το dashboard φαίνεται.
- Με Ctrl+C κλείνει μόνο η σύνδεση. Το bot και το dashboard συνεχίζουν στον server.
- Χρησιμοποιούμε το 8502 για να μη μπερδεύεται με το τοπικό dashboard (8501).

## 9. Παρακολούθηση από το terminal

Συνδέεσαι με `ssh cryptobot` και μετά `cd /opt/cryptopairs`:

| Τι θέλεις | Εντολή |
|---|---|
| Σύνοψη (θέσεις, συναλλαγές, κέρδος) | `docker compose exec bot python -m cryptopairs status` |
| Ζωντανή ροή του bot (Ctrl+C για έξοδο, το bot συνεχίζει) | `docker compose logs -f --tail 50 bot` |
| Αν τρέχουν | `docker compose ps` |
| Εξαγωγή σε CSV | `docker compose exec bot python -m cryptopairs export --out logs/export` |

Για να φέρεις την εξαγωγή στον υπολογιστή σου (π.χ. για το notebook):

```bash
rsync -av cryptobot:/opt/cryptopairs/logs/export/ ~/Documents/CryptoPairsBot/exports/server/
```

## 10. Ενημέρωση κώδικα ή ζευγών

Αφού αλλάξεις κάτι στον υπολογιστή σου:

```bash
cd ~/Documents/CryptoPairsBot
./deploy.sh
```

Η βάση **δεν** χάνεται: το bot συνεχίζει με τις ανοιχτές θέσεις του.

## 11. Διακοπή και νέα αρχή

Στον server (`ssh cryptobot`, `cd /opt/cryptopairs`):

```bash
docker compose stop                      # σταματούν και τα δύο (η βάση μένει)
docker compose start                     # ξαναξεκινούν από εκεί που ήταν
docker compose restart dashboard         # μόνο το dashboard, αν κολλήσει

# μηδενισμός: η παλιά βάση φυλάγεται στο state/old_<ημερομηνία>/
docker compose stop && mkdir -p state/old_$(date +%F) && mv state/paper.db* state/old_$(date +%F)/ && docker compose start
```
