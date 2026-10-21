# 🚀 AURA — 100% Free Public Deployment Guide (Render.com)

This guide explains how you (the platform owner) can deploy your **AURA AI Email Intelligence** web application to the internet for **$0**, allowing any user to visit your site, connect their Gmail, and use your AI triage system.

---

## Step 1: Push to GitHub
Your repository is already configured and synced at:
👉 `https://github.com/Reshma-1717/Automated-Email-Triage-Response-System`

---

## Step 2: Deploy Free on Render.com (Takes 2 Minutes)

1. Go to **[Render.com](https://render.com/)** and sign up for a free account with your GitHub account.
2. Click **"New +"** in the top navigation and select **"Web Service"**.
3. Choose **"Build and deploy from a Git repository"** and select `Automated-Email-Triage-Response-System`.
4. Configure the service settings:
   * **Name:** `aura-email` *(or any name you prefer)*
   * **Region:** Any region close to you (e.g., *Singapore* or *Frankfurt*)
   * **Branch:** `main`
   * **Runtime:** `Python 3`
   * **Build Command:** `pip install -r requirements.txt`
   * **Start Command:** `gunicorn wsgi:app`
   * **Instance Type:** `Free` ($0/month)

5. Under **"Environment Variables"**, add:
   * `OPENAI_API_KEY`: *(Your OpenAI API key)*
   * `API_BASE_URL`: `https://api.openai.com/v1` *(or your custom LLM proxy)*
   * `MODEL_NAME`: `gpt-4o-mini`
   * `OWNER_EMAIL`: `reshmakodavali@gmail.com`
   * `SECRET_KEY`: *(Generate any random string for secure sessions)*
   * `OAUTHLIB_INSECURE_TRANSPORT`: `0` *(Enforces HTTPS in production)*

6. Click **"Create Web Service"**.
   Render will build and deploy your app. Within 2 minutes, you will get a live public URL:
   👉 `https://aura-email.onrender.com`

---

## Step 3: Update Google Cloud OAuth Redirect URI

1. Open your **[Google Cloud Console](https://console.cloud.google.com/)**.
2. Navigate to **APIs & Services** → **Credentials**.
3. Click on your **OAuth 2.0 Client ID**.
4. Under **"Authorized redirect URIs"**, click **"Add URI"** and paste your live callback URL:
   ```
   https://aura-email.onrender.com/callback
   ```
   *(Also keep `http://localhost:5000/callback` for local development).*
5. Click **Save**.

---

## Step 4: Make Google Login Available to Anyone (External Mode)

1. In Google Cloud Console, navigate to **APIs & Services** → **OAuth consent screen**.
2. Under **User Type**, make sure **External** is selected.
3. Under **Publishing status**, click **"Publish App"**.
   * Now, **anyone in the world** with a Google account can sign in!
   * *Note on Unverified Warning:* Since Gmail scopes are sensitive, users will see a one-time message: *"Google hasn't verified this app"*. Users simply click **"Advanced"** → **"Go to Aura (unsafe)"** to connect. This is completely standard for student and indie developer apps before enterprise CASA audits.

---

## Step 5: Access Your Owner Command Center

1. Visit your live site (`https://aura-email.onrender.com`).
2. Log in with `reshmakodavali@gmail.com`.
3. In your dashboard sidebar, you will see the **"👑 Owner Command Center"** button (or visit `/admin`).
4. From here you can:
   * See all registered users across the platform.
   * Monitor live email triage activity in real time.
   * View total emails processed and spam filtered.
   * Ensure systems remain operational.
