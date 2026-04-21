# Stripe Deploy Checklist

Everything you need to take Solvix from "free beta" to "taking money" in one pass. Assumes the ai-proxy Worker is already deployed to Cloudflare and the marketing site lives on `kidquest.fun` (or wherever you point `STRIPE_SUCCESS_URL`).

## 1 · Stripe dashboard setup

### Create 4 recurring prices

Dashboard → **Products** → **+ Add product**. Create two products with two prices each. Copy each resulting `price_…` id — we'll paste them into the Worker env vars below.

| Product  | Price    | Interval | Trial | env var                          |
| -------- | -------- | -------- | ----- | -------------------------------- |
| Family   | $9.99    | monthly  | 7d    | `STRIPE_PRICE_FAMILY_MONTHLY`    |
| Family   | $79.00   | yearly   | 7d    | `STRIPE_PRICE_FAMILY_YEARLY`     |
| Classroom| $29.99   | monthly  | 7d    | `STRIPE_PRICE_CLASSROOM_MONTHLY` |
| Classroom| $249.00  | yearly   | 7d    | `STRIPE_PRICE_CLASSROOM_YEARLY`  |

> Trials are set per-Checkout-session by the Worker (via `STRIPE_TRIAL_DAYS`), so you don't need to configure them on the product itself.

### Create the webhook endpoint

Dashboard → **Developers** → **Webhooks** → **+ Add endpoint**.

- **Endpoint URL:** `https://kidquest-ai-proxy.rhahavy-b.workers.dev/stripe/webhook`
- **Events to send:**
  - `checkout.session.completed`   ← provisions the tenant
  - `customer.subscription.updated` ← flips `suspended` on payment failure
  - `customer.subscription.deleted` ← flips `suspended` on cancellation

Copy the **signing secret** (`whsec_…`) — we set it as a Worker secret below.

### Enable the customer portal

Dashboard → **Settings** → **Billing** → **Customer portal**.

- Turn ON **Allow customers to update payment methods**
- Turn ON **Allow customers to cancel subscriptions**
- Turn ON **Allow customers to switch plans** (optional, lets parents switch family↔classroom)
- Set **Default redirect link** to whatever you use for `STRIPE_PORTAL_RETURN_URL` (e.g. `https://kidquest.fun/app/`)

## 2 · Worker configuration

### Secrets (never in git)

```bash
cd workers/ai-proxy
npx wrangler secret put STRIPE_SECRET_KEY       # sk_live_... or sk_test_...
npx wrangler secret put STRIPE_WEBHOOK_SECRET   # whsec_... from step 1
```

### Env vars (public, in `wrangler.toml`)

Add to the `[vars]` block:

```toml
[vars]
# …existing vars…

STRIPE_PRICE_FAMILY_MONTHLY    = "price_…"
STRIPE_PRICE_FAMILY_YEARLY     = "price_…"
STRIPE_PRICE_CLASSROOM_MONTHLY = "price_…"
STRIPE_PRICE_CLASSROOM_YEARLY  = "price_…"

# Where Stripe sends the user after a successful payment.
# MUST include {CHECKOUT_SESSION_ID} — we use it to look up the PIN.
STRIPE_SUCCESS_URL        = "https://kidquest.fun/thank-you/?session_id={CHECKOUT_SESSION_ID}"

# Where Stripe sends the user if they click "Back" on the Checkout page.
STRIPE_CANCEL_URL         = "https://kidquest.fun/#pricing"

# Where the Stripe billing portal sends them back when they click "Return".
STRIPE_PORTAL_RETURN_URL  = "https://kidquest.fun/app/"

# Free trial length. 7 is the value on the marketing site copy — change
# both if you change one.
STRIPE_TRIAL_DAYS         = "7"
```

### Deploy

```bash
cd workers/ai-proxy
./deploy.sh
```

## 3 · Smoke test (test-mode card `4242 4242 4242 4242`)

1. Visit `https://kidquest.fun/#pricing`
2. Click **Start free trial** on either plan. Type a classroom name. You should land on Stripe Checkout.
3. Pay with the test card (any future expiry, any CVC, any ZIP).
4. Stripe redirects you to `/thank-you/?session_id=cs_test_…`. Within a couple of seconds the page should display your freshly-minted 4-digit PIN.
5. Click **Open Solvix** → enter the PIN → you're in.
6. Teacher Dashboard → **Subscription & Billing** → **Manage Subscription** should open the Stripe customer portal.
7. In the Stripe dashboard, cancel the test subscription. Within ~seconds the Worker flips `tenant.suspended = true`; reload the dashboard and the billing card should show the red "Subscription inactive" banner.

If the thank-you page hangs on "Setting up your classroom…":

- Check **Developers → Webhooks → your endpoint → Events** in Stripe. The `checkout.session.completed` delivery should be 200.
- Check Cloudflare Worker logs (`wrangler tail`) for errors on `/stripe/webhook`.
- The most common cause is a mismatched `STRIPE_WEBHOOK_SECRET` — the Worker will log `invalid_signature` and return 400.

## 4 · Going live

1. Swap `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` for their live-mode equivalents.
2. In live mode, create the 4 prices again (test-mode prices don't carry over) and update all 4 `STRIPE_PRICE_*` env vars.
3. Create a new live-mode webhook endpoint (same URL, same event list).
4. Redeploy with `./deploy.sh`.
5. Do one real $9.99 transaction on your own card to make sure the whole flow works end-to-end in prod. Refund yourself from the Stripe dashboard after.

## Troubleshooting cheatsheet

| Symptom                                            | Likely cause                                        |
| -------------------------------------------------- | --------------------------------------------------- |
| `/stripe/checkout` returns `plan_not_configured`   | One of the four `STRIPE_PRICE_*` env vars is unset  |
| `/stripe/checkout` returns `success_url_not_configured` | `STRIPE_SUCCESS_URL` is missing                |
| Thank-you page 404s forever                        | Webhook isn't firing, or the secret mismatches      |
| Portal button returns `return_url_not_configured`  | `STRIPE_PORTAL_RETURN_URL` is missing               |
| Portal button returns `no_stripe_customer`         | Tenant was manually provisioned, not via Checkout   |
| Webhook logs `invalid_signature`                   | `STRIPE_WEBHOOK_SECRET` doesn't match the dashboard |
