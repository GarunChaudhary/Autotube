# Run AutoTube 24/7 for free on Oracle Cloud

Oracle Cloud "Always Free" gives you a server with 4 CPUs, 24 GB RAM and 200 GB disk at no cost. That's enough
for AutoTube's local AI, voice and video rendering. Total time is about 1 hour, mostly waiting for downloads.

## 1. Tailscale on your PC/phone (5 min)
Tailscale makes the dashboard reachable **only from your own devices**.
1. Go to https://tailscale.com/download, install it on this PC (and your phone if you like), and sign in
   with Google.

## 2. Create the Oracle account (15 min)
1. Go to https://signup.oracle.com.
2. Choose your **Home Region** carefully. It can't be changed later, and free servers are easier to get in less
   busy regions, for example `India West (Mumbai)`, `UK South (London)` or `Germany Central (Frankfurt)`.
3. Oracle asks for a card to verify your identity. Always Free resources are **not charged**. Don't upgrade
   unless you want to.

## 3. Create the free server (10 min)
1. Open the Oracle Cloud console and go to **Compute → Instances → Create instance**.
2. **Image:** click *Change image* and choose **Canonical Ubuntu 24.04**.
3. **Shape:** click *Change shape*, choose **Ampere**, then **VM.Standard.A1.Flex**. Set **4 OCPUs** and
   **24 GB memory** (it shows "Always Free-eligible").
4. **Boot volume:** set it to **100 GB**.
5. **SSH keys:** choose *Generate a key pair for me* and click **Save private key**. Keep that file safe, for
   example as `C:\Users\<you>\.ssh\oracle.key`.
6. Click **Create**. If you see "Out of capacity", try again later or pick another *Availability domain*.
7. When it shows **Running**, copy the **Public IP address**.

Do **not** open any extra ports. AutoTube never needs to be reachable from the internet.

## 4. Make the bundle on this PC (2 min)
In the AutoTube folder:
```
cd frontend && npm run build && cd ..
.venv\Scripts\python.exe scripts\export_for_server.py
```
This creates `autotube_server_bundle.tar.gz` in the folder above AutoTube. It contains your YouTube login,
so treat it like a password.

## 5. Copy it to the server and install (30-40 min, mostly downloads)
In PowerShell on this PC (replace the IP and key path):
```
scp -i C:\Users\<you>\.ssh\oracle.key ..\autotube_server_bundle.tar.gz ubuntu@<PUBLIC_IP>:~
ssh -i C:\Users\<you>\.ssh\oracle.key ubuntu@<PUBLIC_IP>
```
Then, on the server:
```
tar xzf autotube_server_bundle.tar.gz && rm autotube_server_bundle.tar.gz
cd AutoTube && bash deploy/install_server.sh
```
Near the end it prints a Tailscale login link. Open it and log in with the **same** account as step 1.

When it finishes, open **http://autotube** on your PC or phone. That's your dashboard, now running 24/7.

## 6. Turn off the copy on this PC (important)
Only one AutoTube may run, or both will upload the same scheduled videos.
1. On this PC, run `STOP_AUTOTUBE.bat`.
2. In the old dashboard's Scheduler, turn off "Start with Windows" (or delete
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AutoTube AI.vbs`).
3. Delete `autotube_server_bundle.tar.gz` from this PC.

## Good to know
- **Already-finished video files** stay on this PC. They're not moved. Videos that were still in progress will
  be remade on the server.
- **Speed:** the server is slower than a fast PC. Expect Shorts in about 5-10 minutes and long videos in 20-40.
- **Oracle may reclaim** Always Free servers that stay almost idle for 7 days. AutoTube's daily video work
  usually prevents this. To be fully safe, upgrade the account to "Pay As You Go". Always Free resources stay
  free, but watch that you only use free shapes.
- **Reconnect YouTube** (rarely needed): `ssh -i <key> -L 8000:127.0.0.1:8000 ubuntu@<PUBLIC_IP>`, then open
  http://localhost:8000/youtube/ on this PC and click Reconnect.
- **Logs:** `journalctl -u autotube -f`. **Restart:** `sudo systemctl restart autotube`.
