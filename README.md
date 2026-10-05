# Pocket Change History - Telegram hourly bot

1. Create a PRIVATE GitHub repo and upload everything in this folder.
2. Download Anton-Regular.ttf (Google Fonts, free license) and put it in fonts/
3. Telegram: create a bot with @BotFather, add it as ADMIN of your private channel.
   Get the channel id (looks like -100xxxxxxxxxx) e.g. by forwarding a channel post to @getidsbot.
4. Pollinations: get an sk_ key at https://enter.pollinations.ai/keys
5. Repo > Settings > Secrets and variables > Actions:
   Secrets: POLLINATIONS_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
   Variables (optional): TEXT_MODEL, IMAGE_MODEL
   Check available names at https://gen.pollinations.ai/text/models and https://gen.pollinations.ai/image/models
6. Actions tab > hourly-post > Run workflow (test once). After that it runs every hour.

Local test:  set the 3 env vars, then  python bot.py
