# Restoring the RLD from public sources

Everything here is public; no account or help from the project team is needed.
You choose all passwords yourself. Tested with Docker 29 / Compose 5 on 19 Nov 2026.

## 1. Get the parts

```sh
mkdir rld && cd rld
git clone https://github.com/documenting-zargari/drd-django
git clone https://github.com/documenting-zargari/drd-angular
cd drd-django/deploy
mkdir -p data/dump data/audio
```

- **Database dump:** the newest one is published automatically (older ones: the
  [releases page](https://github.com/documenting-zargari/rlb-database/releases)):

  ```sh
  curl -L https://github.com/documenting-zargari/rlb-database/releases/latest/download/rld-arangodump.tar.gz \
    | tar -xz -C data/dump          # → data/dump/rms/
  ```

  With the read-only account you can also dump the live database yourself:
  `arangodump --server.endpoint ssl://arangodb-dev.romanidb.eu:443 --server.username rld-reader --server.database rms --output-directory data/dump/rms`
- **Audio** (optional, a few hours for everything; re-runs only fetch new files):

  ```sh
  python3 ../tools/download_audio.py --dump data/dump/rms --out data/audio --listing
  ```

## 2. Configure

```sh
cp .env.example .env                              # set the three passwords
cp frontend-build.env.example frontend-build.env  # API address baked into the web client
```

The defaults run everything locally: web client on http://localhost:8080, API on
http://localhost:8020. For a real server, point two DNS names at it and follow the
"Real server" lines in `.env.example`; Caddy obtains HTTPS certificates itself.

Optional: speakers' personal data is stored encrypted (`ENC:`). With the salt and
master password from the project, derive the key and put it into `.env`:

```sh
python3 ../tools/derive_encrypt_key.py --salt <64 hex> --check data/dump/rms
```

## 3. Start

```sh
docker compose up -d arangodb postgres
docker compose --profile restore run --rm restore   # loads data/dump/rms (once)
docker compose up -d --build                        # builds API + web client
docker compose exec backend python manage.py createsuperuser
```

The backend runs its migrations and rebuilds the search analyzers on every start.
User accounts are not part of the public dump: create them again in the admin
(http://localhost:8020/admin/) or the web client.

## 4. Check

- http://localhost:8080 shows the samples map and lists; a table and a phrase search return results
- audio plays for a sample whose files are in `data/audio`
- the admin can log in

## Updating to a newer dump

```sh
docker compose down
docker volume rm rld_arango-data
docker compose up -d arangodb
docker compose --profile restore run --rm restore
docker compose up -d
```
