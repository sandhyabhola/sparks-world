# ✨ Sparks World

Sparks World is a **Generative AI application**: LLMs generate and score the thinking challenges, a text-to-image model turns player ideas into artwork, and a multi-layer AI safety pipeline (including romanized Hindi and other Indian-language text) moderates every piece of content.

**A gamified creative-learning platform where teens think, create and share, safely.**

Sparks World turns everyday thinking into a game. Players answer short story-based questions that train reasoning, then climb a level path by creating AI-generated artwork from design tasks. Every piece of content passes through automated safety checks, and the community gallery is anonymous while people vote, so ideas are judged on merit.

> Built with FastAPI, Neon PostgreSQL and vanilla JavaScript. It has no frontend build step and few dependencies, and is designed to be deployed cheaply on a free-tier stack.

---

## Table of Contents

1. [Highlights](#highlights)
2. [How It Works](#how-it-works)
3. [Feature Overview](#feature-overview)
4. [Architecture & Tech Stack](#architecture--tech-stack)
5. [Security & Safety](#security--safety)
6. [Getting Started](#getting-started)
7. [Configuration](#configuration)
8. [API Reference](#api-reference)
9. [Project Structure](#project-structure)
10. [Known Limitations](#known-limitations)
11. [Roadmap](#roadmap)
12. [Future Security Improvements](#future-security-improvements)
13. [License & Contact](#license--contact)

---

## Highlights

- 🧠 **Brain Arena:** AI-written, fully fictional story situations that end with an opinion question. Answers are scored on thinking, not grammar, and work in any language.
- 🗺️ **Level Progression Path:** five themed levels (Neon Lantern, Crystal Labyrinth, Floating Citadel, The Cyber-Grove, Bio-Mech Abyss), unlocked in order, with XP and badges.
- 🎨 **AI Art Studio:** players turn a design task into an image with a text-to-image model. An AI refine step helps improve the idea first.
- 🎲 **Fresh tasks every time:** design tasks (nature, wallpaper, fantasy, seasons, book covers) come from a pool of about 300 combinations. The server records each player's history so **a task is never repeated for the same player**, across levels and quests.
- 🏆 **Quests & Leaderboard:** daily, weekly and monthly quests, a leaderboard, badges and a shop.
- 🕶️ **Anonymous judging:** the gallery and forum hide author names while people vote and comment. Names appear only on the top-designs list, after voting.
- 🛡️ **Safety by design:** prompt moderation, image moderation that fails closed, email-verified accounts and human verification.

---

## How It Works

```
Sign up (email code) ──► Answer story questions ──► Earn XP, unlock levels
                                    │
                                    ▼
          Get a design task ──► Refine idea (AI) ──► Generate art
                                    │
                                    ▼
              Post to gallery ──► Anonymous likes & comments ──► Top designs
```

1. **Sign up** with a username, password and a verified email (or Google sign-in).
2. **Think:** answer a fictional story question. An AI judge scores the answer.
3. **Create:** open a level, receive a design task, refine the idea and generate the image.
4. **Share:** post the design, collect anonymous feedback, complete quests and climb the leaderboard.

---

## Feature Overview

| Area | What's included |
|---|---|
| **Accounts** | Email-verified signup, login, Google sign-in, forgot-password with email code, username and password change, avatar icons, full account deletion |
| **Brain Arena** | AI-generated scenarios by topic, multi-language answers, AI scoring with XP |
| **Levels** | Five sequential levels, rewards, badges, per-player non-repeating design tasks, a "different task" option before drawing |
| **Art Studio** | Prompt refinement, text-to-image generation, daily generation limit, extra credits |
| **Community** | Anonymous gallery, likes, comments, Spotlight (top designs), Think Tank forum, Mind Voices |
| **Progression** | XP, levels, badges, daily/weekly/monthly quests, leaderboard, shop |
| **Compliance** | Terms and privacy consent at signup, consent for sharing summaries, in-app Terms & Privacy page |

---

## Architecture & Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, **FastAPI**, Uvicorn |
| Database | **Neon PostgreSQL** (serverless, free tier) via `psycopg` with a connection pool; generated images are stored in the database |
| Frontend | HTML, CSS, vanilla JavaScript (no build step), SweetAlert2, Font Awesome |
| Story and scoring AI | **Groq** (primary) with **Google Gemini** (fallback) |
| Image generation | **Cloudflare Workers AI** (FLUX.1 schnell) |
| Safety AI | LLM prompt moderation and Gemini vision image moderation |
| Email | **Brevo** transactional API (used on the hosted site), with SMTP as an optional fallback |
| Bot protection | **Cloudflare Turnstile** |
| Auth | Username/password, email OTP, optional Google Identity |

```
 Browser (HTML/JS) ──HTTPS──► FastAPI ──► Neon PostgreSQL (accounts, content, images)
                                 ├──► Groq / Gemini        (stories, scoring, moderation)
                                 ├──► Cloudflare Workers AI (images)
                                 ├──► Brevo API             (verification codes)
                                 └──► Turnstile             (human check)
```

---

## AI/ML Components

| Component | What it does | Approach |
|---|---|---|
| **Scenario generation** | Writes fresh, fully fictional story situations that end in an opinion question | Prompted LLM with controls for topic, length, tone and language; structured JSON output |
| **Answer scoring** | Scores each player's answer from 0 to 10 on thinking quality, not grammar, in any language | LLM-as-a-judge with a strict rubric; the answer is also cleaned for spelling while keeping the player's voice |
| **Idea refinement** | Helps a player improve an idea before generating an image | LLM prompt-refinement step |
| **Image generation** | Turns a design task into artwork | FLUX.1 schnell on Cloudflare Workers AI, with daily limits and credits |
| **Text moderation** | Blocks vulgar words, slurs and harassment, including romanized Hindi, Telugu and Tamil | Layer 1: rule-based filter with leetspeak and repeated-letter handling. Layer 2: LLM classifier |
| **Prompt moderation** | Allows only fictional objects, places, patterns and scenes | LLM classifier; blocks when the checker is unavailable |
| **Image moderation** | Checks every generated picture before it is saved | Vision-model check; the image is rejected if the checker is unavailable (fail closed) |
| **Reliability** | Keeps the app running when a provider fails | Groq as primary model with automatic fallback to Gemini |
| **Task personalisation** | Gives each player design tasks they have never seen | Pool of about 300 generated task combinations with a per-user history so a task never repeats for the same player |

## Security & Safety

Security and child-safety were treated as core requirements, not afterthoughts.

### Account & data protection
- **Password hashing:** PBKDF2-HMAC-SHA256 with a per-user salt and 200,000 iterations. Plain passwords are never stored.
- **Hashed session tokens:** tokens are random 192-bit values, stored only as hashes and expiring after 7 days.
- **Email privacy:** emails are stored as keyed HMAC hashes, not in readable form, and Gmail dots and `+tags` are normalised. This enforces **one email, one account**, and an email is freed only after account deletion.
- **Email verification:** a 6-digit code with a 10-minute expiry. Codes are stored hashed and compared in constant time (`hmac.compare_digest`).
- **Account deletion:** removes the user's art, answers, comments, likes, sessions and task history.

### Abuse prevention
- **Human verification:** Cloudflare Turnstile on signup and on password reset.
- **Rate limiting:** limits on code requests per IP and per email, a global daily send cap, and a wrong-code lockout per device.
- **Generation limits:** a daily image limit per user, with credits beyond it.

### Content safety
- **Prompt moderation:** a profanity filter plus an AI check that allows only fictional objects, places, patterns and scenes, with no real people, brands or sensitive symbols.
- **Image moderation:** every generated image is checked by a vision model before it is saved. If the checker is unavailable, **the image is rejected, not published** (fail closed).
- **Anonymity in review:** author names are hidden in the gallery and forum.

### Web hardening
- Security headers: `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`.
- Secrets live in `.env` (or the host's environment settings) and are git-ignored. `EMAIL_SECRET` keys the email hashing and must never change.

---

## Getting Started

**Requirements:** Python 3.10+ and a free [Neon](https://neon.tech) PostgreSQL database

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env        # then fill in DATABASE_URL and your keys (see Configuration)

# 3. Run
python -m uvicorn main:app --reload
```

Open **http://127.0.0.1:8000**. All database tables are created automatically on first run.

> Features whose keys are not set degrade gracefully. For example, art generation reports that the image service is not set up yet.

---

## Configuration

Copy `.env.example` to `.env` and set:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Neon PostgreSQL connection string (ends with `?sslmode=require`). **Required** |
| `EMAIL_SECRET` | Long random string that keys the email hashing. Set once, never change. Locally it is auto-created in `.email_secret` if unset |
| `GROQ_API_KEY` | Primary LLM for stories and scoring |
| `GEMINI_API_KEY` | Fallback LLM and image moderation |
| `GROQ_MODEL`, `GEMINI_MODEL` | Model overrides (optional) |
| `CF_ACCOUNT_ID`, `CF_API_TOKEN` | Cloudflare Workers AI for image generation |
| `BREVO_API_KEY`, `MAIL_FROM_EMAIL` | Sends verification codes via Brevo (sender must be verified in Brevo). Needed on Render, which blocks SMTP |
| `SMTP_EMAIL`, `SMTP_APP_PASSWORD` | Optional Gmail fallback for local use |
| `SMTP_HOST`, `SMTP_PORT` | Use a provider other than Gmail (optional) |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET` | Human verification (strongly recommended) |
| `GOOGLE_CLIENT_ID` | Enables Google sign-in (optional) |

⚠️ Never commit `.env` or `.email_secret`. Changing `EMAIL_SECRET` makes existing accounts' hashed emails stop matching, so back it up.

---

## API Reference

All endpoints are under `/api` and, except signup and login, require a bearer session token.

| Group | Endpoints |
|---|---|
| **Auth** | `POST /signup/start`, `/signup/resend`, `/signup/verify`, `/login`, `/logout`, `/google`, `/forgot/start`, `/forgot/verify` |
| **Account** | `GET /me`, `POST /account/username`, `/account/password`, `/icon`, `/consent`, `DELETE /account` |
| **Brain Arena** | `GET /topics`, `/scenario`, `POST /answer` |
| **Levels & Quests** | `GET /levels`, `/level/{n}/task`, `/challenges`, `/quests`, `/badges` |
| **Art** | `POST /art/generate`, `/art/refine`, `/art/{id}/post`, `/art/unlock`, `GET /art/mine`, `DELETE /art/{id}` |
| **Community** | `GET /gallery`, `/art/top`, `/minds`, `/thinktank`, `/forum`, `/leaderboard`, `/stats`, plus like and comment routes |

---

## Project Structure

```
Sparks_out/
├── main.py            # FastAPI app: auth, game logic, AI integrations, moderation
├── requirements.txt
├── .env.example       # configuration template
├── db_compat.py       # PostgreSQL layer: pooling, reconnects, SQL translation
├── render.yaml        # Render deployment settings
└── static/
    ├── login.html     # landing, signup, login, password reset
    ├── app.html       # main application (dashboard onwards)
    ├── terms.html     # terms & privacy policy
    ├── style.css      # shared theme
    ├── app.css        # app-page styling
    └── app.js
```

---

## Known Limitations

Listed openly because they are the first things to address before a large public launch.

- **Free-tier limits:** Neon's free plan has 1 GB of storage and pauses when idle (first request after a pause is slower), and Render's free plan sleeps after 15 idle minutes. Fine for a prototype or small community.
- **Images in the database:** images are compressed (about 110 KB each) and stored in PostgreSQL. Moving them to object storage would be the next step at larger scale.
- **In-memory rate limiting:** counters reset on restart and are not shared across multiple servers.
- **Relaxed CSP:** the policy permits inline scripts (`unsafe-inline`) because the frontend uses inline code. Moving to nonces or external scripts would tighten it.
- **Sessions in browser storage:** the token lives in `localStorage`. Moving to `HttpOnly` cookies would reduce XSS exposure.
- **AI dependence:** story and moderation quality depend on third-party model availability.

---

## Roadmap

### Near term
- **Premium plan:** more daily generations, priority generation, exclusive badges and profile themes.
- **Advanced design tools:** inpainting and "edit this region", image-to-image variations, upscaling, background removal, aspect-ratio presets (phone wallpaper, poster, book cover).
- **Style packs:** selectable art styles such as anime-inspired, hand-painted storybook, watercolour, pixel art, low-poly and cinematic, each as a guided preset.
- **Level and quest content:** more levels, seasonal events, themed weekly contests, and team challenges.

### Mid term
- **Character and portrait creation:** human and anime-style characters through a dedicated, age-appropriate pipeline with stricter moderation (see below). This removes today's "no people" rule only after the safeguards exist.
- **Creator tools:** collections and portfolios, downloadable high-resolution exports, print-ready covers, and a remix feature to branch from a public design with credit.
- **Learning layer:** a skill tree with feedback on composition, colour and storytelling, and a teacher or classroom mode with private groups.
- **Social features:** follow system, curated challenges, moderator-reviewed community spotlights.
- **Mobile and PWA:** installable app, offline-friendly screens, push reminders for daily quests.

### Data & ML (planned)
- **Analytics dashboard:** score distributions, levels completed, popular design categories, and daily active users.
- **LLM-judge evaluation:** compare AI scores against human-labelled answers and report agreement, to measure and improve scoring quality.
- **Multilingual toxicity classifier:** a trained classifier for romanized Hindi, Telugu and Tamil text, benchmarked against the current word list and LLM check on precision, recall and F1.
- **Task recommendation:** suggest the next design task from a player's likes and completion history.
- **A/B testing:** measure how product changes, such as the "different task" button, affect posting and retention.

### Long term
- Multi-language UI, accessibility audit (WCAG), analytics dashboard for educators, a public API for schools and partners, and a creator marketplace with revenue share.

---

## Future Security Improvements

| Priority | Improvement |
|---|---|
| **High** | `HttpOnly`, `Secure`, `SameSite` cookie sessions in place of `localStorage` tokens, with CSRF protection |
| **High** | Nonce-based strict CSP and removal of inline scripts |
| **High** | Distributed rate limiting (Redis) and per-account login throttling with progressive lockout |
| **High** | Migrate to PostgreSQL and object storage (S3 or R2) with signed, expiring image URLs |
| **Medium** | Optional two-factor authentication (TOTP or passkeys), with new-device alerts |
| **Medium** | Argon2id password hashing with transparent rehash on login |
| **Medium** | Dedicated image-safety stack, including a second-opinion classifier, hash-matching, and a human review queue with report and takedown flows |
| **Medium** | Audit logs, structured security monitoring, automated dependency and secret scanning in CI |
| **Medium** | Secrets management (a vault or the host's secret store) and key rotation |
| **For human/anime generation** | Block real-person likeness and public-figure prompts, enforce adult-only character depictions, add age gating and parental-consent options, watermark or provenance metadata (C2PA) on generated images, and a clear content policy |
| **Compliance** | Privacy-law readiness (GDPR, COPPA, India's DPDP Act): data-export, consent logs, retention policy, and a published data-processing summary |

---

## License & Contact

© Sparks World. All rights reserved. For demos, collaboration or licensing enquiries, please get in touch with the project owner.


---

## Deployment (free tier)

| Part | Service | Notes |
|---|---|---|
| Database | Neon PostgreSQL | Stores accounts, posts, votes and images, so nothing is lost on restarts |
| Host | Render free web service | Settings in `render.yaml`; use the **same region as Neon** (for example Ohio for AWS US East 2) |
| Email | Brevo API | Free 300 emails/day; verify a sender address first |

1. Push the repository to GitHub (`.env`, `.email_secret` and virtual environments are git-ignored).
2. On Render create a Web Service from the repo. Build: `pip install -r requirements.txt`. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
3. Add the variables from `.env.example` under Environment (`DATABASE_URL`, `EMAIL_SECRET`, AI keys, Cloudflare, Turnstile, Brevo).
4. Add the live URL to the Turnstile hostnames and, if used, Google's authorized origins.
5. Replace `YOUR-APP-URL` in the link-preview tags with the live URL.