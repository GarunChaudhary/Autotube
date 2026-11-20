AUTOTUBE AI - SERVER DEPLOYMENT FOLDER
======================================

WHAT IS IN THIS FOLDER
  autotube_server_bundle.tar.gz   The whole app: code, built dashboard, your settings (.env),
                                  database, topic banks and YouTube login (re-encrypted for the server).
                                  KEEP IT PRIVATE - it works like a password.
  DEPLOY_TO_SERVER.bat            Double-click: uploads the bundle and installs (or updates) AutoTube.
  DEPLOY_TO_SERVER.ps1            The script the .bat runs.
  README_DEPLOY.txt               This file.

WHAT THE SERVER NEEDS
  Ubuntu 22.04 or 24.04 (64-bit, Intel/AMD or ARM), 4+ CPUs, 8+ GB RAM (16 GB better), 40+ GB free disk.
  SSH access with a user that can use sudo. No GPU needed. Do NOT open port 8000 to the internet.

FIRST INSTALL
  1. Install Tailscale on this PC (and your phone): https://tailscale.com/download - sign in.
  2. Double-click DEPLOY_TO_SERVER.bat. Enter the server IP, the SSH user and your SSH key file.
  3. Wait 30-40 minutes. When a Tailscale login link appears, open it and sign in with the SAME account.
  4. Open http://autotube in your browser. Log in with your usual dashboard username and password.
  5. IMPORTANT - stop the copy on this PC, or videos get uploaded twice:
       - run D:\Garun\RE\Re\AutoTube\STOP_AUTOTUBE.bat
       - delete  %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AutoTube AI.vbs  (if it exists)

UPDATING THE SERVER LATER (after changes on this PC)
  1. Make a fresh bundle (in PowerShell):
       cd D:\Garun\RE\Re\AutoTube\frontend; npm run build; cd ..
       .venv\Scripts\python.exe scripts\export_for_server.py
       Move-Item -Force D:\Garun\RE\Re\autotube_server_bundle.tar.gz D:\Garun\RE\Re\AutoTube_Server_Deploy\
  2. Double-click DEPLOY_TO_SERVER.bat. It detects the existing install and replaces only the app code.
     The server's database, settings and YouTube login are kept.

USEFUL COMMANDS ON THE SERVER (after: ssh user@SERVER_IP)
  sudo systemctl status autotube        is it running?
  journalctl -u autotube -f             live log
  sudo systemctl restart autotube       restart

RECONNECT YOUTUBE (rarely needed)
  On this PC:  ssh -L 8000:127.0.0.1:8000 user@SERVER_IP
  Then open http://localhost:8000/youtube/ and click Reconnect.

NOTES
  - Finished video files stay on this PC; videos still in progress are remade on the server.
  - The bundle was last rebuilt on 17 Nov 2026 (after the cartoon / kids' stories feature was removed).
