# Restoring the RLD from public sources

Everything here is public; no account or help from the project team is needed.
You choose all passwords yourself. Tested with Docker 29 / Compose 5 on 19 Nov 2026.

## 1. Get the parts

```sh
mkdir rld && cd rld
git clone -b dev https://github.com/documenting-zargari/drd-django
git clone -b dev https://github.com/documenting-zargari/drd-angular
cd drd-django/deploy
mkdir -p data/dump data/audio
```

- **Database dump:** the newest one is published automatically (older ones: the
  [releases page](https://github.com/documenting-zargari/rlb-database/releases)):

  ```sh
  curl -L https://github.com/documenting-zargari/rlb-database/releases/latest/download/rld-arangodump.tar.gz \
    | tar -xz -C data/dump          # → data/dump/rms/
  curl -L -o data/rld-users.json.enc \
    https://github.com/documenting-zargari/rlb-database/releases/latest/download/rld-users.json.enc
  ```

  `rld-users.json.enc` holds the user accounts and their roles (encrypted, see below).

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

### Encryption key (personal data and user accounts)

Speakers' personal data (`ENC:` fields) and the user export are encrypted. The
key, `RMS_ENCRYPT_KEY`, is never published. You derive it from:

- the **master password**: the only secret. The project gives it to you once,
  on a separate channel (not with these instructions).
- the **salt**: not secret, included as `tools/encrypt_salt.txt`.

```sh
pip install cryptography
python3 ../tools/derive_encrypt_key.py --check data/dump/rms
# asks for the master password, checks it against the dump, prints RMS_ENCRYPT_KEY=...
```

With the right password, `--check` reports that all encrypted values decrypt
("N encrypted values decrypt, 0 do not") before printing the key. A wrong
password stops with "The key does not decrypt the data". The salt in
`tools/encrypt_salt.txt` together with the master password was verified this way
on 19 Nov 2026.

Put the printed line into `.env`. Without it everything else works, but personal
data shows as `ENC:` and the user accounts cannot be imported.

## 3. Start

```sh
docker compose up -d arangodb postgres
docker compose --profile restore run --rm restore   # loads data/dump/rms (once)
docker compose up -d --build                        # builds API + web client
docker compose cp data/rld-users.json.enc backend:/tmp/   # needs RMS_ENCRYPT_KEY
docker compose exec backend python manage.py import_users /tmp/rld-users.json.enc
docker compose exec backend python manage.py createsuperuser  # your own admin account
```

The backend runs its migrations and rebuilds the search analyzers on every start.
`import_users` restores all accounts with their roles and (hashed) passwords.
`createsuperuser` makes you the RLD admin: only admins see hidden samples and the
decrypted personal data.

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
