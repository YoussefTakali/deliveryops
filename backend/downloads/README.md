# Desktop installers

Drop the built desktop installer(s) here. The backend serves the most recent one at
`GET /api/download/desktop`, and the web app's **Télécharger l'app bureau** page links to it.

Build one from `desktop/`:

```bash
cd desktop
npm install
npm run dist        # -> desktop/dist/DeliveryOps Setup <version>.exe  (Windows NSIS installer)
```

then copy the produced `.exe` into this folder:

```bash
cp "desktop/dist/DeliveryOps Setup 0.1.0.exe" backend/downloads/
```

This folder is bind-mounted into the backend container (see docker-compose), so no rebuild is needed
— the download appears as soon as the file is here. Installer binaries are gitignored.
