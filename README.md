# 📬 Crypto Trend Mailer

Lightweight single-file Python CLI that aggregates trending, newly launched, and top-gaining cryptocurrencies from public APIs and automatically emails a clean, professional HTML digest to you or your subscribers.

## 🎯 Why this exists

Stay on top of the crypto market without opening ten tabs. One script, one command, one email in your inbox with everything that matters right now.

## ✨ What you get

- 🔥 Trending coins from CoinGecko
- 🆕 Brand-new launches from DexScreener
- 📅 Recently launched picks (last 1–2 weeks)
- 📈 Top 24h gainers from KuCoin
- 💰 Top 24h volume from CryptoCompare
- 🏆 Top by market cap from CoinMarketCap (optional API key)
- 📨 Four email backends: Gmail, Brevo, Resend, Custom SMTP
- 👥 Subscriber list for daily blasts
- 🎨 Colour terminal output, polished HTML email
- 🪶 Pure Python standard library. Zero pip installs.

## ⚙️ Getting started

1. Clone the repo:
   ```bash
   git clone https://github.com/krainium/Crypto-Trend-Mailer.git
   cd Crypto-Trend-Mailer
   ```

2. Run it:
   ```bash
   python3 cryptomailer.py
   ```

3. Pick option **6) Settings** the first time. Choose your email provider, drop in the credentials when asked. For Gmail you need a 16-character App Password from your Google account, not your normal login password.

That is the whole setup.

## 🚀 How to use it

The menu is self-explanatory:

| # | Action |
|---|--------|
| 1 | Send a digest right now to one address |
| 2 | Manage subscribers (add, list, remove) |
| 3 | Run the daily digest for every subscriber |
| 4 | Preview the next email as an HTML file |
| 5 | Test the email connection with a small ping |
| 6 | Settings (provider, sender, defaults) |
| 0 | Quit |

For a true daily run, drop this in a cron job:

```bash
0 9 * * * cd /path/to/Crypto-Trend-Mailer && printf "3\n0\n" | python3 cryptomailer.py
```

That fires the digest to all subscribers every morning at 9.

## 📨 Pick your email backend

| Provider | What you need |
|----------|---------------|
| 📧 Gmail | Gmail address + 16-char App Password |
| 📨 Brevo | Brevo API key (free tier: 300 emails/day) |
| ⚡ Resend | Resend API key + verified sending domain |
| 🛠️ Custom SMTP | Host, port, username, password |

Brevo, Resend ride on HTTPS so they work even on hosts where SMTP ports get blocked (looking at you, OVH, AWS, Google Cloud).

## 📊 Where the data comes from

All five public APIs work with no key required. CoinMarketCap is the lone exception. Skip it if you do not have a key. The script will move on without complaint.

## 🗂️ About the config file

A `config.json` lives next to the script after first run. It stores your provider choice, sender info, defaults, subscriber list. Secrets like passwords or API keys never get written to disk. You re-enter them per session for safety.

## 💡 Good to know

- 🌱 New sender? Mark the first email as "not spam" so it lands in the inbox next time.
- 🔢 Bump "coins per category" in Settings to see more picks per section.
- 🌐 Stuck behind a firewall? Pick Brevo or Resend instead of Gmail SMTP.

---

Issues, PRs, suggestions all welcome.
