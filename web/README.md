# videogen web

- Setup: `npm install`, set `DATABASE_URL` in `.env.local`, then `npm run db:init` (needs local Postgres, DB `videogen`).
- Dev: `npx next dev`
- Prod: `npm run build && npm start -- -p 3100`, open http://localhost:3100
- Jobs run `../videogen.py` as a child process; videos land in `outputs/<id>.mp4`. Max 2 active jobs (429 beyond that).
- On server start, leftover queued/running jobs are marked failed ("server restarted").
